const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function smoke() {
  const [base] = process.argv.slice(2);
  const credentials = JSON.parse(process.env.CASE_SMOKE_CREDENTIALS);
  const folder = process.env.CASE_SMOKE_SCREENSHOTS;
  const browser = await chromium.launch({
    executablePath: process.env.CASE_SMOKE_CHROME || undefined,
    args: ["--disable-background-networking", "--disable-sync", "--no-first-run"],
  });
  const errors = [];
  try {
    async function connect(name, viewport) {
      const page = await browser.newPage({ viewport });
      page.setDefaultTimeout(15000);
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(base + "/analyst/");
      await page.getByLabel("Analyst token").fill(credentials[name].token);
      await page.getByRole("button", { name: "Connect", exact: true }).click();
      await page.getByRole("heading", { name: "Cases", exact: true }).waitFor();
      return page;
    }

    const page = await connect("console-admin-qa", { width: 1440, height: 1050 });
    await page.getByRole("button", { name: "Load linked demo cases" }).click();
    await page.getByText("Linked demo cases are ready to investigate.").waitFor();
    assert.equal(await page.locator("#queue-list li").count(), 4);
    assert.equal(await page.getByLabel("Scope").inputValue(), "connected");
    assert.match(await page.locator("#graph-status").textContent(), /2 linked cases/);
    const linked = page.getByRole("list", { name: "Linked cases in this graph" });
    assert.equal(await linked.getByRole("button").count(), 2);
    assert.doesNotMatch(await linked.textContent(), /control/);
    const pathToSouth = linked.locator("li").filter({ hasText: "Open batch cross-case-demo-south" });
    assert.match(await pathToSouth.locator(".case-path").textContent(), /shared-device.*beneficiary-bank.*demo-south/);
    if (folder) await page.screenshot({ path: path.join(folder, "connected-cases-desktop.png"), fullPage: true });

    await page.getByLabel("Scope").selectOption("current");
    await page.getByRole("button", { name: "Payment: demo-payment-1", exact: true }).waitFor();
    assert.equal(await linked.isVisible(), false);

    await page.getByLabel("Scope").selectOption("connected");
    const west = page.getByRole("button", { name: "Payment: demo-payment-1 · cross-case-demo-west", exact: true });
    await west.click();
    await page.waitForFunction(() => document.querySelector("#case-meta").textContent.includes("cross-case-demo-west"));
    await page.waitForFunction(() => Array.from(document.querySelectorAll("#graph-nodes button")).some(
      (node) => node.textContent.includes("demo-payment-1 · cross-case-demo-west") && node.getAttribute("aria-pressed") === "true"
    ));

    await linked.getByRole("button", { name: /Open batch cross-case-demo-south/ }).click();
    await page.waitForFunction(() => document.querySelector("#case-meta").textContent.includes("cross-case-demo-south"));
    await page.getByLabel("Reason").fill("Reviewed the beneficiary bank links from two other batches.");
    await page.getByRole("button", { name: "Record decision" }).click();
    await page.getByText("Review recorded.", { exact: true }).waitFor();
    assert.equal(await page.locator("#case-state").textContent(), "In review");
    assert.match(await page.locator("#history").textContent(), /console-admin-qa/);

    const viewer = await connect("console-viewer-qa", { width: 375, height: 850 });
    await viewer.locator("#queue-list").getByText("cross-case-demo-east", { exact: true }).click();
    await viewer.getByLabel("Depth").selectOption("4");
    await viewer.getByLabel("Scope").selectOption("connected");
    await viewer.getByRole("button", { name: /Open batch cross-case-demo-south/ }).waitFor();
    assert.equal(await viewer.getByRole("button", { name: "Record decision" }).isVisible(), false);
    assert.equal(await viewer.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (folder) await viewer.screenshot({ path: path.join(folder, "connected-cases-mobile.png"), fullPage: true });
    assert.deepEqual(errors, []);
    console.log("Real Neo4j browser flow passed: linked intake, scope toggle, cross-case payment pivot, linked-case navigation, review history, read-only role, and mobile layout.");
  } finally {
    await browser.close();
  }
}

smoke().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
