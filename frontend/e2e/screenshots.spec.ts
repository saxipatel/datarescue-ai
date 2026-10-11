import { expect, test } from "@playwright/test";

// Captures README screenshots from a running stack. Opt-in: SCREENSHOTS=1 npx playwright test screenshots
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture docs screenshots");

const OUT = process.env.SCREENSHOT_DIR ?? "../docs/images";

test("capture console pages", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/login");
  await page.getByLabel("Email").fill("steward@example.test");
  await page.getByLabel("Password").fill("datarescue-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();

  await page.goto("/");
  await expect(page.getByText("Pipeline health")).toBeVisible();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${OUT}/overview.png`, fullPage: true });

  await page.goto("/incidents?view=all");
  await page.getByRole("link", { name: /Duplicate event IDs/ }).first().click();
  await expect(page.getByText("Suspected causes")).toBeVisible();
  await page.screenshot({ path: `${OUT}/incident.png`, fullPage: true });

  await page.goto("/recovery");
  await page.getByRole("button", { name: /skip identical duplicate/ }).first().click();
  await expect(page.getByText(/Dry run/)).toBeVisible();
  await page.screenshot({ path: `${OUT}/recovery-proposal.png`, fullPage: true });

  await page.goto("/recovery");
  await page.getByRole("button", { name: /^[0-9a-f]{8}$/ }).first().click();
  await expect(page.getByText("Audit trail")).toBeVisible();
  await page.screenshot({ path: `${OUT}/recovery-job.png`, fullPage: true });

  await page.goto("/lineage");
  await page.getByLabel("Highlight impact of incident").selectOption({ index: 1 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/lineage.png`, fullPage: true });

  await page.goto("/failure-lab");
  await expect(page.getByText("Recent runs")).toBeVisible();
  await page.screenshot({ path: `${OUT}/failure-lab.png`, fullPage: true });
});
