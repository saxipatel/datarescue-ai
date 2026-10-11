import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// The README walkthrough, end to end in a real browser against the running stack.
const PASSWORD = "datarescue-demo";

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  // The login page lists demo emails too, so wait for something only the app shell has.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

test("inject duplicates, diagnose, approve, recover, verify", async ({ page }) => {
  await signIn(page, "engineer@example.test");
  await page.goto("/");
  await expect(page.getByText("Pipeline health")).toBeVisible();
  await expect(page.getByText("payments_core").first()).toBeVisible();

  // Failure Lab: 10% duplicate event IDs in a 1,000-row batch (the defaults).
  await page.getByRole("link", { name: "Failure Lab" }).click();
  await page.getByLabel("Scenario").selectOption("duplicate_event_ids");
  await page.getByLabel("Batch size").fill("1000");
  await page.getByLabel("Anomaly %").fill("10");
  await page.getByRole("button", { name: "Inject" }).click();
  await expect(page.getByRole("cell", { name: "skipped" })).toBeVisible();
  await page.getByRole("button", { name: "Inspect incident" }).click();

  // Incident: deterministic diagnosis, evidence and downstream lineage.
  await expect(page.getByRole("heading", { name: /Duplicate event IDs/ })).toBeVisible();
  await expect(page.getByText("non-idempotent upstream retry")).toBeVisible();
  await expect(page.getByRole("cell", { name: /dashboard_revenue/ }).first()).toBeVisible();
  const incidentUrl = page.url();

  // Propose the recommended action and dry run it.
  await page.getByRole("button", { name: "Propose", exact: true }).click();
  await page.getByLabel("Justification").fill("Identical redelivery confirmed by hash (e2e)");
  await page.getByRole("button", { name: "Create proposal" }).click();
  await expect(page).toHaveURL(/\/recovery\?proposal=/);
  const proposalUrl = page.url();
  await page.getByRole("button", { name: "Run dry run" }).click();
  await expect(page.getByText("would update")).toBeVisible();
  await expect(page.getByText(/Waiting for a steward/)).toBeVisible();

  // Steward approves and executes.
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "steward@example.test");
  await page.goto(proposalUrl);
  await page.getByRole("button", { name: "Approve" }).click();
  await page.getByRole("button", { name: "Execute recovery" }).click();
  await expect(page).toHaveURL(/\/recovery\?job=/);
  await expect(page.getByText("no_duplicate_event_ids")).toBeVisible();
  await expect(page.getByText("business_data_unchanged")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Verification/ })).toContainText("passed");

  // Same idempotency key again returns the finished job.
  await page.goto(proposalUrl);
  await page.getByRole("button", { name: "Submit again with the same key" }).click();
  await expect(page).toHaveURL(/\/recovery\?job=/);

  await page.goto(incidentUrl);
  await expect(page.getByText("resolved", { exact: true }).first()).toBeVisible();
});

test("analyst is read-only", async ({ page }) => {
  await signIn(page, "analyst@example.test");
  await page.getByRole("link", { name: "Failure Lab" }).click();
  await expect(page.getByText(/read-only here/)).toBeVisible();
  await page.getByRole("link", { name: "Audit & Settings" }).click();
  await expect(page.getByText(/visible to admins/)).toBeVisible();
});

test("no horizontal page scroll at phone width", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signIn(page, "steward@example.test");
  // Detail pages use the most recent records; the walkthrough test above guarantees some exist.
  const ids = await page.evaluate(async () => {
    const h = { headers: { Authorization: `Bearer ${sessionStorage.getItem("datarescue.token")}` } };
    const first = async (path: string) => (await (await fetch(`/api/v1${path}`, h)).json()).items[0]?.id;
    return {
      batch: await first("/batches?limit=1"),
      incident: await first("/incidents?limit=1"),
      proposal: await first("/repairs/proposals?limit=1"),
      job: await first("/recoveries?limit=1"),
    };
  });
  const paths = [
    "/", "/batches", `/batches/${ids.batch}`, "/incidents", `/incidents/${ids.incident}`,
    "/quarantine", "/recovery", `/recovery?proposal=${ids.proposal}`, `/recovery?job=${ids.job}`,
    "/lineage", "/failure-lab", "/rules", "/audit",
  ];
  for (const path of paths) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${path} scrolls horizontally`).toBeLessThanOrEqual(0);
  }
});
