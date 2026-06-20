use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::Command as ProcessCommand;
use std::thread::sleep;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, anyhow, bail};
use chrono::{DateTime, Local, NaiveDate};
use clap::{Parser, Subcommand, ValueEnum};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

mod acceptance;
mod accepted_drafts;
mod browser_ops;
mod cli;
mod commands;
mod model;
mod pending;
mod playwriter;
mod reports;
mod run_ops;
mod salesnav;
mod store;
#[cfg(test)]
mod tests;
mod util;

pub(crate) use acceptance::*;
pub(crate) use accepted_drafts::*;
pub(crate) use browser_ops::*;
pub(crate) use cli::*;
pub(crate) use commands::*;
pub(crate) use model::*;
pub(crate) use pending::*;
pub(crate) use playwriter::*;
pub(crate) use reports::*;
pub(crate) use run_ops::*;
pub(crate) use salesnav::*;
pub(crate) use store::*;
pub(crate) use util::*;

const APP_DIR: &str = "linkedin-network-run";

fn main() -> Result<()> {
    let cli = Cli::parse();
    let store = Store::new(cli.state_dir)?;
    dispatch(&store, cli.command)
}
