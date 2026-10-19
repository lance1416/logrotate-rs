//! Read-only metadata checks; configuration contents and state are not opened.

use std::{fmt, fs, io, path::PathBuf};

use crate::cli::Mode;

#[derive(Debug)]
pub enum RunError {
    MissingConfig,
    Metadata { path: PathBuf, source: io::Error },
    UnsupportedEntry { path: PathBuf, kind: &'static str },
    NotImplemented(Mode),
}

impl fmt::Display for RunError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingConfig => write!(f, "at least one CONFIG file or directory is required"),
            Self::Metadata { path, source } => match source.kind() {
                io::ErrorKind::NotFound => write!(f, "configuration entry {path:?} does not exist"),
                io::ErrorKind::PermissionDenied => {
                    write!(
                        f,
                        "cannot access configuration entry {path:?}: permission denied"
                    )
                }
                _ => write!(f, "cannot inspect configuration entry {path:?}: {source}"),
            },
            Self::UnsupportedEntry { path, kind } => write!(
                f,
                "configuration entry {path:?} is a {kind}; expected a regular file or directory"
            ),
            Self::NotImplemented(Mode::Execute) => write!(f, "executor is not implemented yet"),
            Self::NotImplemented(Mode::DryRun) => {
                write!(
                    f,
                    "dry-run planner is not implemented yet; no plan was produced"
                )
            }
        }
    }
}

impl std::error::Error for RunError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Metadata { source, .. } => Some(source),
            _ => None,
        }
    }
}

fn entry_kind(kind: fs::FileType) -> &'static str {
    #[cfg(unix)]
    {
        use std::os::unix::fs::FileTypeExt;
        if kind.is_fifo() {
            return "FIFO";
        }
        if kind.is_socket() {
            return "socket";
        }
        if kind.is_char_device() {
            return "character device";
        }
        if kind.is_block_device() {
            return "block device";
        }
    }
    let _ = kind;
    "special file"
}

pub fn validate_entries(configs: &[PathBuf]) -> Result<(), RunError> {
    if configs.is_empty() {
        return Err(RunError::MissingConfig);
    }
    for path in configs {
        let metadata = fs::metadata(path).map_err(|source| RunError::Metadata {
            path: path.clone(),
            source,
        })?;
        if !metadata.is_file() && !metadata.is_dir() {
            return Err(RunError::UnsupportedEntry {
                path: path.clone(),
                kind: entry_kind(metadata.file_type()),
            });
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abnormal_path_is_an_io_error_without_panicking() {
        assert!(matches!(
            validate_entries(&[PathBuf::from("\0")]),
            Err(RunError::Metadata { .. })
        ));
    }
}
