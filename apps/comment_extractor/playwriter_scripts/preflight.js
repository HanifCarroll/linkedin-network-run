const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  state.linkedinToolsPage =
    context.pages().find((candidate) => candidate.url().includes("linkedin.com")) ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

const activePage = await getPage();
fs.writeFileSync(
  config.out,
  `${JSON.stringify({ status: "ready", url: activePage.url() }, null, 2)}\n`
);
