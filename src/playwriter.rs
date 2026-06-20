use crate::*;

pub(crate) fn is_bunx_path(path: &PathBuf) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name == "bunx" || name == "bunx.exe")
}

pub(crate) fn playwriter_command(playwriter: &PathBuf) -> ProcessCommand {
    let mut command = ProcessCommand::new(playwriter);
    if is_bunx_path(playwriter) {
        command.arg("playwriter@latest");
    }
    command
}

pub(crate) fn run_playwriter_config(
    playwriter: &PathBuf,
    session: &str,
    config_js: &str,
) -> Result<()> {
    let mut command = playwriter_command(playwriter);
    let status = command
        .args(["-s", session, "-e", config_js])
        .status()
        .context("running Playwriter config")?;
    if !status.success() {
        bail!("Playwriter config command failed with {status}");
    }
    Ok(())
}

pub(crate) fn run_playwriter_file(
    playwriter: &PathBuf,
    session: &str,
    script: &PathBuf,
) -> Result<()> {
    run_playwriter_file_with_timeout(playwriter, session, script, 45_000)
}

pub(crate) fn run_playwriter_file_with_timeout(
    playwriter: &PathBuf,
    session: &str,
    script: &PathBuf,
    timeout_ms: u32,
) -> Result<()> {
    let mut command = playwriter_command(playwriter);
    let timeout = timeout_ms.to_string();
    let status = command
        .args(["-s", session, "--timeout", timeout.as_str(), "-f"])
        .arg(script)
        .status()
        .context("running Playwriter script")?;
    if !status.success() {
        bail!("Playwriter script failed with {status}");
    }
    Ok(())
}
