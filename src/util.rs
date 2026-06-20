pub(crate) fn percentage_suffix(numerator: u32, denominator: u32) -> String {
    if denominator == 0 {
        return String::new();
    }
    format!(
        " ({:.1}%)",
        f64::from(numerator) * 100.0 / f64::from(denominator)
    )
}

pub(crate) fn format_duration_ms(duration_ms: u64) -> String {
    if duration_ms < 1000 {
        return format!("{duration_ms}ms");
    }
    let seconds = duration_ms as f64 / 1000.0;
    if seconds < 60.0 {
        return format!("{seconds:.1}s");
    }
    format!("{:.1}m", seconds / 60.0)
}

pub(crate) fn normalize_linkedin_url(url: &str) -> String {
    let trimmed = url.trim();
    trimmed
        .split(['?', '#'])
        .next()
        .unwrap_or(trimmed)
        .trim_end_matches('/')
        .to_string()
}
