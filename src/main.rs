//! Entry point for the Rust reimplementation of logrotate.

mod cli;
mod input;

use std::fmt;
use std::io::{self, Write};
use std::process::ExitCode;

fn diagnostic(error: impl fmt::Display, code: u8) -> ExitCode {
    let _ = writeln!(io::stderr().lock(), "logrotate-rs: {error}");
    ExitCode::from(code)
}

fn information(text: fmt::Arguments<'_>) -> ExitCode {
    match io::stdout().lock().write_fmt(text) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => diagnostic(error, 1),
    }
}

fn main() -> ExitCode {
    match cli::parse(std::env::args_os().skip(1)) {
        Ok(cli::Command::Help) => information(format_args!("{}", cli::HELP)),
        Ok(cli::Command::Version) => information(format_args!(
            "{} {}\n",
            env!("CARGO_PKG_NAME"),
            env!("CARGO_PKG_VERSION")
        )),
        Ok(cli::Command::Run(options)) => {
            if options.verbose {
                let _ = writeln!(
                    io::stderr().lock(),
                    "logrotate-rs: mode={:?}, force={}, state={:?} (not opened); checking entry metadata only",
                    options.mode,
                    options.force,
                    options.state
                );
            }
            let error = match input::validate_entries(&options.configs) {
                Ok(()) => input::RunError::NotImplemented(options.mode),
                Err(error) => error,
            };
            diagnostic(error, 1)
        }
        Err(error) => diagnostic(error, 2),
    }
}
