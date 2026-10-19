//! Argument parsing only: no filesystem access.

use std::ffi::OsString;
use std::fmt;
use std::path::PathBuf;

pub const DEFAULT_STATE: &str = "/var/lib/logrotate-rs/status";
pub const HELP: &str = "Usage: logrotate-rs [OPTIONS] CONFIG [CONFIG ...]

Options:
  -h, --help          Print help
      --version       Print the Cargo package version
  -d, --debug, --dry-run
                      Select read-only mode (planner not implemented)
  -f, --force         Record force intent (executor not implemented)
  -s, --state PATH    Select state path (not opened)
                      Default: /var/lib/logrotate-rs/status
  -v, --verbose       Write detailed diagnostics to stderr
      --              Treat remaining arguments as configuration paths

Current capability: configuration entry metadata checks only.
Configuration parsing, planning and log rotation are not implemented.
Normal and dry-run requests return error 1; no successful plan is produced.
";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Execute,
    DryRun,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Options {
    pub configs: Vec<PathBuf>,
    pub state: PathBuf,
    pub mode: Mode,
    pub force: bool,
    pub verbose: bool,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            configs: Vec::new(),
            state: PathBuf::from(DEFAULT_STATE),
            mode: Mode::Execute,
            force: false,
            verbose: false,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    Help,
    Version,
    Run(Options),
}

#[derive(Debug, PartialEq, Eq)]
pub enum ParseError {
    UnknownOption(OsString),
    NonUtf8Option(OsString),
    MissingStateValue,
    EmptyStateValue,
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownOption(option) => write!(f, "unknown or unsupported option {option:?}"),
            Self::NonUtf8Option(option) => write!(f, "non-UTF-8 option token {option:?}"),
            Self::MissingStateValue => write!(f, "--state (-s) requires a PATH value"),
            Self::EmptyStateValue => write!(f, "--state (-s) requires a nonempty PATH value"),
        }
    }
}

impl std::error::Error for ParseError {}

fn set_state(options: &mut Options, value: OsString) -> Result<(), ParseError> {
    if value.is_empty() {
        return Err(ParseError::EmptyStateValue);
    }
    options.state = PathBuf::from(value);
    Ok(())
}

fn next_state(args: &mut impl Iterator<Item = OsString>) -> Result<OsString, ParseError> {
    let value = args.next().ok_or(ParseError::MissingStateValue)?;
    if value.as_encoded_bytes().starts_with(b"-") && value != "-" {
        return Err(ParseError::MissingStateValue);
    }
    Ok(value)
}

pub fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Command, ParseError> {
    let mut args = args.into_iter();
    let mut options = Options::default();
    while let Some(arg) = args.next() {
        if arg == "--" {
            options.configs.extend(args.map(PathBuf::from));
            break;
        }
        if !arg.as_encoded_bytes().starts_with(b"-") || arg == "-" {
            options.configs.push(PathBuf::from(arg));
            continue;
        }
        let token = arg
            .to_str()
            .ok_or_else(|| ParseError::NonUtf8Option(arg.clone()))?;
        match token {
            "--help" => return Ok(Command::Help),
            "--version" => return Ok(Command::Version),
            "--debug" | "--dry-run" => options.mode = Mode::DryRun,
            "--force" => options.force = true,
            "--verbose" => options.verbose = true,
            "--state" => set_state(&mut options, next_state(&mut args)?)?,
            _ if token.starts_with("--state=") => {
                set_state(&mut options, OsString::from(&token[8..]))?;
            }
            _ if token.starts_with("--") => return Err(ParseError::UnknownOption(arg)),
            _ => {
                let shorts = &token[1..];
                for (index, short) in shorts.char_indices() {
                    match short {
                        'h' => return Ok(Command::Help),
                        'd' => options.mode = Mode::DryRun,
                        'f' => options.force = true,
                        'v' => options.verbose = true,
                        's' => {
                            let rest = &shorts[index + 1..];
                            let value = if rest.is_empty() {
                                next_state(&mut args)?
                            } else {
                                OsString::from(rest)
                            };
                            set_state(&mut options, value)?;
                            break;
                        }
                        _ => return Err(ParseError::UnknownOption(arg)),
                    }
                }
            }
        }
    }
    Ok(Command::Run(options))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parsed(args: &[&str]) -> Command {
        parse(args.iter().map(OsString::from)).unwrap()
    }

    fn options(args: &[&str]) -> Options {
        let Command::Run(options) = parsed(args) else {
            panic!("expected Run")
        };
        options
    }

    #[test]
    fn c06_order_spaces_and_option_terminator() {
        let result = options(&["first conf", "-v", "second", "--", "-third", "--help"]);
        assert_eq!(
            result.configs,
            ["first conf", "second", "-third", "--help"].map(PathBuf::from)
        );
        assert!(result.verbose);
    }

    #[test]
    fn c07_defaults_aliases_and_state_forms() {
        assert_eq!(options(&[]), Options::default());
        for dry in ["-d", "--debug", "--dry-run"] {
            let result = options(&["--force", dry, "--verbose", "config"]);
            assert_eq!(result.mode, Mode::DryRun);
            assert!(result.force && result.verbose);
            assert_eq!(result.state, PathBuf::from(DEFAULT_STATE));
        }
        for form in [
            vec!["-s", "state path"],
            vec!["--state", "state path"],
            vec!["--state=state path"],
            vec!["-sstate path"],
        ] {
            assert_eq!(options(&form).state, PathBuf::from("state path"));
        }
    }

    #[test]
    fn repeated_and_clustered_options_have_consistent_meanings() {
        let result = options(&["-dfv", "-f", "--verbose", "-sfirst", "-vssecond"]);
        assert_eq!(result.mode, Mode::DryRun);
        assert!(result.force && result.verbose);
        assert_eq!(result.state, PathBuf::from("second"));
        assert_eq!(options(&["--state=-value"]).state, PathBuf::from("-value"));
        assert_eq!(options(&["-s", "-"]).state, PathBuf::from("-"));
    }

    #[test]
    fn help_and_version_return_immediately_but_do_not_hide_prior_errors() {
        assert_eq!(parsed(&["missing", "-vh", "--bad"]), Command::Help);
        assert_eq!(parsed(&["--version", "--bad"]), Command::Version);
        assert_eq!(parsed(&["--help", "--version"]), Command::Help);
        assert_eq!(parsed(&["--version", "--help"]), Command::Version);
        assert!(matches!(
            parse([OsString::from("--bad"), OsString::from("--help")]),
            Err(ParseError::UnknownOption(_))
        ));
    }

    #[test]
    fn c04_c05_invalid_options_and_missing_or_empty_state() {
        for option in [
            "--mail",
            "--log",
            "--skip-state-lock",
            "--wait-for-state-lock",
            "--force=yes",
            "--help=yes",
            "-V",
            "-x",
            "-文",
        ] {
            assert_eq!(
                parse([OsString::from(option)]),
                Err(ParseError::UnknownOption(option.into()))
            );
        }
        for args in [
            vec!["-s"],
            vec!["--state"],
            vec!["-s", "--help"],
            vec!["--state", "--"],
        ] {
            assert_eq!(
                parse(args.into_iter().map(OsString::from)),
                Err(ParseError::MissingStateValue)
            );
        }
        for args in [vec!["--state="], vec!["-s", ""]] {
            assert_eq!(
                parse(args.into_iter().map(OsString::from)),
                Err(ParseError::EmptyStateValue)
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn c12_native_paths_are_retained_and_non_utf8_options_are_rejected() {
        use std::os::unix::ffi::OsStringExt;
        let path = OsString::from_vec(b"path-\xff".to_vec());
        let Command::Run(result) = parse(["-s".into(), path.clone(), path.clone()]).unwrap() else {
            panic!("expected Run")
        };
        assert_eq!(result.state.as_os_str(), path);
        assert_eq!(result.configs[0].as_os_str(), path);
        let option = OsString::from_vec(b"--state=\xff".to_vec());
        assert_eq!(
            parse([option.clone()]),
            Err(ParseError::NonUtf8Option(option.clone()))
        );
        assert_eq!(
            parse(["--help".into(), option.clone()]).unwrap(),
            Command::Help
        );
        assert!(matches!(
            parse([option, "--help".into()]),
            Err(ParseError::NonUtf8Option(_))
        ));
    }
}
