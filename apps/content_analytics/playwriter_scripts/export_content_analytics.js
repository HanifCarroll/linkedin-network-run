const fs = require("node:fs");

const config = JSON.parse(
  fs.readFileSync(state.linkedinContentAnalyticsConfigPath, "utf8"),
);

function writeReceipt(payload) {
  fs.writeFileSync(config.receiptOut, `${JSON.stringify(payload, null, 2)}\n`);
}

async function observe(activePage, search) {
  console.log("URL:", activePage.url());
  console.log(
    "Page logs:",
    await getLatestLogs({ page: activePage, sinceLastCall: true }),
  );
  console.log(await snapshot({ page: activePage, search }));
}

async function exactOne(locator, label) {
  const count = await locator.count();
  if (count !== 1) {
    throw new Error(`${label} contract changed: expected 1 exact match, found ${count}`);
  }
  return locator;
}

async function main() {
  const analyticsUrl = String(config.analyticsUrl || "");
  if (analyticsUrl !== "https://www.linkedin.com/analytics/creator/content/") {
    throw new Error("analytics URL does not match the verified LinkedIn contract");
  }

  if (
    !state.linkedinContentAnalyticsPage ||
    state.linkedinContentAnalyticsPage.isClosed()
  ) {
    const pages = context.pages();
    state.linkedinContentAnalyticsPage =
      pages.find((candidate) => candidate.url() === "about:blank") ||
      (await context.newPage());
  }
  const activePage = state.linkedinContentAnalyticsPage;

  if (activePage.url() !== analyticsUrl) {
    await activePage.goto(analyticsUrl, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
  }
  await waitForPageLoad({ page: activePage, timeout: 15000 });
  await observe(activePage, /Content analytics|Export|7 days/);

  if (activePage.url() !== analyticsUrl) {
    throw new Error(
      `LinkedIn analytics navigation contract changed: ${activePage.url()}`,
    );
  }

  const exportLink = await exactOne(
    activePage.getByRole("link", { name: "Export", exact: true }),
    "Export link",
  );
  const dateRangeButton = await exactOne(
    activePage.getByRole("button", { name: "7 days", exact: true }),
    "7 days range button",
  );
  const dateRange = (await dateRangeButton.textContent()).trim();
  if (dateRange !== "7 days") {
    throw new Error(`date range contract changed: ${JSON.stringify(dateRange)}`);
  }

  const confirmationText =
    "Your analytics export is being prepared. Confirm to begin downloading.";
  const confirmation = activePage.getByText(confirmationText, { exact: true });
  const confirmationCount = await confirmation.count();
  if (confirmationCount > 1) {
    throw new Error(
      `confirmation dialog contract changed: found ${confirmationCount} exact messages`,
    );
  }
  if (confirmationCount === 0) {
    await exportLink.click();
    await observe(activePage, /Export|Confirm|Cancel|being prepared/);
  }

  await confirmation.waitFor({ state: "visible", timeout: 15000 });
  const confirmButton = await exactOne(
    activePage.getByRole("button", { name: "Confirm", exact: true }),
    "Confirm button",
  );

  await confirmButton.click();
  await confirmation.waitFor({ state: "hidden", timeout: 15000 });
  console.log(
    "Page logs:",
    await getLatestLogs({ page: activePage, sinceLastCall: true }),
  );

  writeReceipt({
    status: "confirmation_clicked",
    analyticsUrl,
    dateRange,
    selectorContract: {
      export: { role: "link", name: "Export", exact: true },
      confirm: { role: "button", name: "Confirm", exact: true },
      dateRange: { role: "button", name: "7 days", exact: true },
      confirmationText,
    },
  });
  state.linkedinContentAnalyticsPage.removeAllListeners();
}

await main().catch((error) => {
  writeReceipt({
    status: "failed",
    error: String(error && error.stack ? error.stack : error),
  });
  throw error;
});
