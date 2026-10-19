//! Unix CLI boundary checks; all product calls have a timeout and use synthetic inputs.
#![cfg(unix)]

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fs;
use std::io;
use std::os::unix::ffi::OsStringExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime};

static NEXT: AtomicU64 = AtomicU64::new(0);

struct Sandbox(PathBuf);

#[derive(Debug, PartialEq, Eq)]
struct Entry {
    kind: fs::FileType,
    mode: u32,
    modified: SystemTime,
    content: Option<Vec<u8>>,
    target: Option<PathBuf>,
}

impl Sandbox {
    fn new() -> Self {
        loop {
            let path = std::env::temp_dir().join(format!(
                "lrcli-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            match fs::DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => return Self(path),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => panic!("cannot create fixture {path:?}: {error}"),
            }
        }
    }

    fn file(&self, name: impl AsRef<Path>, data: &[u8]) -> PathBuf {
        let path = self.0.join(name);
        fs::write(&path, data).unwrap();
        path
    }

    fn fifo(&self, name: impl AsRef<Path>) -> PathBuf {
        let path = self.0.join(name);
        // Fixture creation only. The product never launches this helper.
        assert!(
            Command::new("mkfifo")
                .arg(&path)
                .status()
                .unwrap()
                .success()
        );
        path
    }

    fn snapshot(&self) -> BTreeMap<PathBuf, Entry> {
        fn visit(root: &Path, path: &Path, tree: &mut BTreeMap<PathBuf, Entry>) {
            let metadata = fs::symlink_metadata(path).unwrap();
            let kind = metadata.file_type();
            tree.insert(
                path.strip_prefix(root).unwrap().to_owned(),
                Entry {
                    kind,
                    mode: metadata.mode(),
                    modified: metadata.modified().unwrap(),
                    content: if kind.is_file() {
                        Some(fs::read(path).unwrap())
                    } else {
                        None
                    },
                    target: if kind.is_symlink() {
                        Some(fs::read_link(path).unwrap())
                    } else {
                        None
                    },
                },
            );
            if kind.is_dir() {
                for child in fs::read_dir(path).unwrap() {
                    visit(root, &child.unwrap().path(), tree);
                }
            }
        }
        let mut tree = BTreeMap::new();
        visit(&self.0, &self.0, &mut tree);
        tree
    }

    fn run(&self, args: &[impl AsRef<OsStr>]) -> Output {
        let mut child = Command::new(env!("CARGO_BIN_EXE_logrotate-rs"))
            .args(args)
            .current_dir(&self.0)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if child.try_wait().unwrap().is_some() {
                return child.wait_with_output().unwrap();
            }
            if Instant::now() >= deadline {
                child.kill().unwrap();
                let output = child.wait_with_output().unwrap();
                panic!("CLI blocked for five seconds: {output:?}");
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    fn checked(&self, args: &[impl AsRef<OsStr>], code: i32, diagnostic: &str) -> Output {
        let before = self.snapshot();
        let output = self.run(args);
        assert_eq!(output.status.code(), Some(code), "{output:?}");
        assert!(
            String::from_utf8(output.stderr.clone())
                .unwrap()
                .contains(diagnostic),
            "{output:?}"
        );
        assert_eq!(
            before,
            self.snapshot(),
            "product modified fixture contents, names, modes or mtimes"
        );
        if code != 0 {
            assert!(
                output.stdout.is_empty(),
                "failed request produced stdout: {output:?}"
            );
        }
        output
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn c01_help_is_successful_and_describes_current_limits() {
    let sandbox = Sandbox::new();
    for help in ["-h", "--help"] {
        let output = sandbox.checked(&[help], 0, "");
        assert!(output.stderr.is_empty());
        let text = String::from_utf8(output.stdout).unwrap();
        for token in [
            "Usage:",
            "CONFIG",
            "--help",
            "--version",
            "--debug",
            "--dry-run",
            "--force",
            "--state",
            "--verbose",
            "not implemented",
        ] {
            assert!(text.contains(token), "help missing {token}");
        }
    }
}

#[test]
fn c02_version_comes_from_cargo() {
    let sandbox = Sandbox::new();
    let output = sandbox.checked(&["--version"], 0, "");
    assert_eq!(
        output.stdout,
        format!("logrotate-rs {}\n", env!("CARGO_PKG_VERSION")).as_bytes()
    );
    assert!(output.stderr.is_empty());
}

#[test]
fn c03_no_config_is_a_request_error() {
    Sandbox::new().checked(&[] as &[&str], 1, "at least one CONFIG");
}

#[test]
fn c04_unsupported_options_are_parse_errors_even_after_config_paths() {
    let sandbox = Sandbox::new();
    sandbox.file("config", b"not parsed\n");
    for option in [
        "--mail",
        "--log",
        "--skip-state-lock",
        "--wait-for-state-lock",
        "--unknown",
        "--force=true",
        "-V",
        "-x",
    ] {
        sandbox.checked(
            &["--state", "absent/status", "config", option],
            2,
            "unknown or unsupported option",
        );
    }
}

#[test]
fn c05_missing_or_empty_state_value_is_a_parse_error() {
    let sandbox = Sandbox::new();
    for args in [
        vec!["-s"],
        vec!["--state"],
        vec!["--state="],
        vec!["-s", ""],
        vec!["-s", "--help"],
        vec!["--state", "--"],
    ] {
        sandbox.checked(&args, 2, "PATH value");
    }
}

#[test]
fn c06_multiple_spaced_and_dash_prefixed_paths_are_accepted_as_entries() {
    let sandbox = Sandbox::new();
    for name in ["first conf", "second conf", "-third"] {
        sandbox.file(name, b"not parsed\n");
    }
    sandbox.checked(
        &["first conf", "-v", "second conf", "--", "-third"],
        1,
        "executor is not implemented",
    );
}

#[test]
fn c07_flags_and_state_never_enable_execution_or_state_access() {
    let sandbox = Sandbox::new();
    sandbox.file("config", b"not parsed\n");
    for name in [
        "state",
        "state.lock",
        "state.pending",
        "active.log",
        "active.log.1",
    ] {
        sandbox.file(name, b"unchanged synthetic sentinel\n");
    }
    sandbox.checked(
        &["-fv", "--state", "state", "config"],
        1,
        "executor is not implemented",
    );
    for dry in ["-d", "--debug", "--dry-run"] {
        let output = sandbox.checked(
            &["-fv", dry, "--state", "state", "config"],
            1,
            "planner is not implemented",
        );
        let stderr = String::from_utf8(output.stderr).unwrap();
        assert!(stderr.contains("force=true") && stderr.contains("not opened"));
    }
    sandbox.checked(&["-v", "config"], 1, "/var/lib/logrotate-rs/status");
}

#[test]
fn c08_missing_and_non_directory_parents_have_path_specific_diagnostics() {
    let sandbox = Sandbox::new();
    sandbox.checked(
        &["missing.conf"],
        1,
        "configuration entry \"missing.conf\" does not exist",
    );
    sandbox.file("file", b"regular file\n");
    let output = sandbox.checked(
        &["file/child"],
        1,
        "cannot inspect configuration entry \"file/child\"",
    );
    assert!(
        !String::from_utf8(output.stderr)
            .unwrap()
            .contains("not implemented")
    );
}

#[test]
fn c08_inaccessible_parent_reports_permission_denied() {
    let sandbox = Sandbox::new();
    if fs::metadata(&sandbox.0).unwrap().uid() == 0 {
        eprintln!("permission-denied fixture requires an unprivileged test user");
        return;
    }
    let parent = sandbox.0.join("private");
    fs::create_dir(&parent).unwrap();
    sandbox.file("private/config", b"not parsed\n");
    let before = sandbox.snapshot();
    let original = fs::metadata(&parent).unwrap().permissions();
    fs::set_permissions(&parent, fs::Permissions::from_mode(0o000)).unwrap();
    let output = sandbox.run(&["private/config"]);
    fs::set_permissions(&parent, original).unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(
        stderr.contains("private/config") && stderr.contains("permission denied"),
        "{stderr}"
    );
    assert_eq!(before, sandbox.snapshot());
}

#[test]
fn c08_fifo_and_socket_are_rejected_without_opening_or_blocking() {
    let sandbox = Sandbox::new();
    sandbox.fifo("fifo");
    let _socket = UnixListener::bind(sandbox.0.join("s")).unwrap();
    sandbox.checked(&["fifo"], 1, "is a FIFO");
    sandbox.checked(&["s"], 1, "is a socket");
    std::os::unix::fs::symlink("/dev/null", sandbox.0.join("device")).unwrap();
    sandbox.checked(&["device"], 1, "is a character device");
}

#[test]
fn c09_valid_file_and_directory_fail_explicitly_without_parsing_or_writing() {
    let sandbox = Sandbox::new();
    sandbox.file(
        "config",
        b"\xff\0include does-not-exist\ncreateolddir\npostrotate\ntouch ran\nendscript\n",
    );
    fs::create_dir(sandbox.0.join("configs")).unwrap();
    sandbox.fifo("configs/unreadable-as-config");
    for path in ["config", "configs"] {
        sandbox.checked(
            &["--state=absent/status", path],
            1,
            "executor is not implemented",
        );
        sandbox.checked(
            &["--dry-run", "-sabsent/status", path],
            1,
            "planner is not implemented",
        );
    }
    // Metadata is accessible even when file contents / directory enumeration are not.
    for path in ["config", "configs"] {
        let path = sandbox.0.join(path);
        let original = fs::metadata(&path).unwrap().permissions();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o000)).unwrap();
        let output = sandbox.run(&[path.as_os_str()]);
        fs::set_permissions(&path, original).unwrap();
        assert_eq!(output.status.code(), Some(1));
        assert!(
            String::from_utf8(output.stderr)
                .unwrap()
                .contains("executor is not implemented")
        );
    }
}

#[test]
fn c10_help_and_version_ignore_business_inputs_and_follow_documented_precedence() {
    let sandbox = Sandbox::new();
    sandbox.fifo("fifo");
    for info in ["-h", "--help", "--version"] {
        for args in [
            vec![info, "--state", "absent/status", "missing", "fifo"],
            vec!["fifo", "missing", "--state", "absent/status", info],
        ] {
            let output = sandbox.checked(&args, 0, "");
            assert!(output.stderr.is_empty());
        }
    }
    sandbox.checked(&["--help", "--unknown"], 0, "");
    sandbox.checked(&["--unknown", "--help"], 2, "unknown or unsupported option");
}

#[test]
fn c11_invalid_later_entry_leaves_prior_inputs_and_sidecars_unchanged() {
    let sandbox = Sandbox::new();
    for name in [
        "config",
        "active.log",
        "active.log.1",
        "state",
        "state.lock",
        "state.pending",
    ] {
        sandbox.file(name, b"unchanged synthetic sentinel\n");
    }
    for flags in [vec!["-f"], vec!["--dry-run"]] {
        let mut args = flags;
        args.extend(["--state", "state", "config", "missing-later"]);
        sandbox.checked(
            &args,
            1,
            "configuration entry \"missing-later\" does not exist",
        );
    }
}

#[test]
fn c12_non_utf8_paths_are_not_replaced_and_option_errors_do_not_panic() {
    let sandbox = Sandbox::new();
    let config = OsString::from_vec(b"config-\xff".to_vec());
    // Linux can represent this name. macOS rejects its creation with EILSEQ;
    // there the request must diagnose the original path rather than substitute.
    if cfg!(target_os = "linux") {
        sandbox.file(Path::new(&config), b"native path\n");
    }
    // A lossy conversion would inspect this FIFO instead of the regular file.
    sandbox.fifo("config-\u{fffd}");
    let state = OsString::from_vec(b"state-\xfe".to_vec());
    sandbox.file("config", b"not parsed\n");
    let output = sandbox.checked(
        &[
            OsString::from("-vs"),
            state.clone(),
            OsString::from("config"),
        ],
        1,
        "executor is not implemented",
    );
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .contains(&format!("{state:?}"))
    );
    let output = sandbox.checked(std::slice::from_ref(&config), 1, "");
    let stderr = String::from_utf8(output.stderr).unwrap();
    if cfg!(target_os = "linux") {
        assert!(stderr.contains("executor is not implemented"));
    } else {
        assert!(stderr.contains(&format!("{:?}", Path::new(&config))));
    }
    assert!(
        !stderr.contains("FIFO"),
        "lossy conversion accessed another entry"
    );
    sandbox.checked(&[config.clone(), OsString::from("--help")], 0, "");
    sandbox.checked(&[OsString::from("--help"), config], 0, "");
    let invalid_option = OsString::from_vec(b"--state=\xff".to_vec());
    sandbox.checked(
        std::slice::from_ref(&invalid_option),
        2,
        "non-UTF-8 option token",
    );
    sandbox.checked(&[OsString::from("--help"), invalid_option.clone()], 0, "");
    sandbox.checked(
        &[invalid_option, OsString::from("--help")],
        2,
        "non-UTF-8 option token",
    );
    sandbox.checked(&[""], 1, "configuration entry \"\"");
}
