import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// Captures the README screenshots (docs/images) from a running stack that already has demo
// data, e.g. after `make demo-chaos`. Opt-in: SCREENSHOTS=1 npx playwright test screenshots
// Pages are only read, but signing in records a login audit event.
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture docs screenshots");

const OUT = process.env.SCREENSHOT_DIR ?? "../docs/images";

type Item = Record<string, unknown> & { id: string };

async function capture(page: Page, file: string, clipHeight?: number) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator('[role="status"][aria-label="Loading"]')).toHaveCount(0);
  await page.waitForTimeout(700); // let charts finish layout
  const clip = clipHeight ? { x: 0, y: 0, width: 1440, height: clipHeight } : undefined;
  await page.screenshot({ path: `${OUT}/${file}`, clip });
}

test("capture README screenshots", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@example.test"); // the audit log is admin only
  await page.getByLabel("Password").fill("datarescue-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();

  // Find the records the README shows: the latest duplicate-ID incident, a batch with
  // quarantined currencies, and a currency repair job.
  const token = await page.evaluate(() => sessionStorage.getItem("datarescue.token"));
  const list = async (path: string): Promise<Item[]> => {
    const res = await page.request.get(`/api/v1${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.ok()).toBeTruthy();
    return (await res.json()).items;
  };
  const incident = (await list("/incidents?limit=1&offset=0&q=Duplicate%20event%20IDs"))[0];
  const quarantined = (await list("/quarantine?limit=1&offset=0&code=CURRENCY_NOT_ALLOWED"))[0];
  const job = (await list("/recoveries?limit=200&offset=0")).find((r) =>
    (r.verification as { checks: { name: string }[] } | null)?.checks.some(
      (c) => c.name === "matches_dry_run",
    ),
  );
  expect(incident && quarantined && job, "run `make demo-chaos` first").toBeTruthy();

  await page.goto("/");
  await expect(page.getByText("Pipeline health")).toBeVisible();
  await capture(page, "overview-dashboard.png");

  await page.goto("/batches");
  await expect(page.getByText("Pipelines & Batches").first()).toBeVisible();
  await capture(page, "pipelines-batches.png");

  await page.goto(`/batches/${incident.batch_id}`);
  await expect(page.getByText("Row accounting")).toBeVisible();
  await capture(page, "batch-detail-duplicate-test.png");

  await page.goto(`/incidents/${incident.id}`);
  await expect(page.getByText("Suspected causes")).toBeVisible();
  await capture(page, "incident-diagnosis.png");

  await page.goto(`/quarantine?batch_id=${quarantined.batch_id}`);
  await expect(page.getByText(/CURRENCY_/).first()).toBeVisible();
  await capture(page, "quarantine-records.png");

  await page.getByRole("button", { name: "Inspect" }).first().click();
  await expect(page.getByText("Raw payload")).toBeVisible();
  await capture(page, "quarantine-record-detail.png");
  await page.keyboard.press("Escape");

  await page.goto(`/recovery?job=${job!.id}`);
  await expect(page.getByText("matches_dry_run")).toBeVisible();
  await capture(page, "recovery-verification.png");

  await page.goto(`/lineage?incident=${incident.id}`);
  await expect(page.getByText("Dependency graph")).toBeVisible();
  await capture(page, "data-lineage.png");

  await page.goto("/failure-lab");
  await expect(page.getByText("Recent runs")).toBeVisible();
  await capture(page, "failure-lab.png", 640);

  await page.goto("/audit");
  await expect(page.getByText("Audit log")).toBeVisible();
  await capture(page, "audit-log.png");

  await page.getByLabel("Entity type").selectOption("recovery_job");
  await expect(page.getByText("recovery.succeeded").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => {
    const top = document.querySelector("section:last-of-type")!.getBoundingClientRect().top;
    window.scrollTo(0, top + window.scrollY - 80);
  });
  await capture(page, "audit-log-recovery.png");
});
