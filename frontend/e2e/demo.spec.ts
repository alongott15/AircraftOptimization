/** Demo flows on the golden data (TESTING.md §5.2, PRD §11). Clock frozen at T0 = 2026-01-15T08:00:00Z. */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { E2E_PASSWORD } from '../playwright.config';

async function login(page: Page, username: string) {
  await page.goto('/');
  await page.getByLabel('שם משתמש').fill(username);
  await page.getByLabel('סיסמה').fill(E2E_PASSWORD);
  await page.getByRole('button', { name: 'התחבר' }).click();
  await expect(page.getByRole('heading', { name: 'לוח בקרה' })).toBeVisible();
}

/** Visible text outside <bdi>/<code> must not contain Latin words (allow-list for aviation acronyms). */
async function latinOutsideBdi(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const allowed = /^(VFR|IFR|MEL|ATA|PAX)$/;
    const out: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const el = node.parentElement;
      if (!el || el.closest('bdi, code, script, style, svg, [aria-hidden="true"], canvas')) continue;
      const style = getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const text = (node.textContent ?? '').replace(/\u2066[^\u2069]*\u2069/g, ''); // LTR isolates = <bdi>
      for (const word of text.match(/[A-Za-z]{2,}/g) ?? []) {
        if (!allowed.test(word)) out.push(word);
      }
    }
    return out;
  });
}

test('dashboard: Hebrew RTL, expired banner and KPIs', async ({ page }) => {
  await login(page, 'planner');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('html')).toHaveAttribute('lang', 'he');
  await expect(page.getByTestId('expired-banner')).toContainText('מטוסים עם פריטים שפג תוקפם: 4');
  await expect(page.getByTestId('kpi-expired')).toContainText('4');
  await expect(page.getByTestId('kpi-grounding')).toContainText('1');
  await expect(page.getByTestId('kpi-unknown')).toContainText('1');
  const noHorizontalScroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  expect(noHorizontalScroll).toBe(true);
  expect(await latinOutsideBdi(page)).toEqual([]);
  const axe = await new AxeBuilder({ page }).analyze();
  const serious = axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const details = serious.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')} — ${n.failureSummary ?? ''}`));
  expect(details).toEqual([]);
});

test('fleet filter "expired" lists exactly the expired aircraft', async ({ page }) => {
  await login(page, 'ops');
  await page.goto('/fleet?compliance_status=EXPIRED');
  const rows = page.locator('[data-testid="fleet-table"] tbody tr');
  await expect(rows).toHaveCount(4);
  const tails = await rows.evaluateAll((trs) => trs.map((tr) => tr.getAttribute('data-tail')));
  expect(tails.sort()).toEqual(['4X-J02', '4X-P05', '4X-S03', '4X-S07']);
  expect(await latinOutsideBdi(page)).toEqual([]);
});

test('aircraft 4X-J04: 3D (or 2D fallback) highlights the grounding MA on engine 2', async ({ page }) => {
  await login(page, 'ops');
  await page.goto('/aircraft/4X-J04');
  await expect(page.getByTestId('aircraft-title')).toContainText('4X-J04');
  const viewer = page.locator('[data-testid="aircraft-viewer"], [data-testid="aircraft-fallback"]');
  await expect(viewer).toBeVisible();
  const hotspot = page.locator('button.ao-hotspot[data-zone="ENGINE_2"]');
  await expect(hotspot).toHaveAttribute('data-tone', 'EXPIRED');
  await hotspot.click();
  await expect(viewer).toHaveAttribute('data-selected-zone', 'ENGINE_2');
  await expect(page.getByTestId('ma-list')).toContainText('MA-2026-00412');
});

test('mission M-A: plan in Hebrew, see reasons, assign', async ({ page }) => {
  await login(page, 'planner');
  await page.getByRole('link', { name: 'תכנון משימה' }).click();
  await page.getByTestId('mission-name').fill('אימון לילה');
  await page.getByTestId('mission-count').fill('2');
  await page.getByTestId('mission-quals').click();
  await page.getByRole('option', { name: 'טיסת לילה' }).click();
  await page.keyboard.press('Escape');
  await page.getByText('חד-מנועי', { exact: true }).click();
  await page.getByTestId('mission-start').fill('2026-01-15T12:00');
  await page.getByTestId('mission-duration').fill('2.5');
  await page.getByTestId('mission-tg').fill('6');
  await page.getByTestId('mission-base').click();
  await page.getByRole('option', { name: 'בסיס צפון' }).click();
  await page.getByTestId('optimize-btn').click();

  await expect(page.getByTestId('result-summary')).toContainText('נבחרו 2 מתוך 4 מטוסים כשירים');
  await expect(page.getByTestId('recommended-set').getByTestId('result-card')).toHaveCount(2);
  await expect(page.getByTestId('disclaimer').first()).toBeVisible();

  const s05 = page.locator('[data-testid="excluded-list"] [data-tail="4X-S05"]');
  await s05.getByRole('button').first().click();
  await expect(s05).toContainText('בדיקת כני נסע תפוג במהלך המשימה');
  await expect(s05).toContainText('כשיר אם יבוצעו עד 4 נחיתות (כולל גע-וסע)');

  await page.getByTestId('assign-btn').click();
  await page.getByTestId('confirm-assign').click();
  await expect(page.getByTestId('mission-status')).toHaveText('שובצה');
});

test('viewer role cannot plan missions', async ({ page }) => {
  await login(page, 'ops');
  await expect(page.getByRole('link', { name: 'תכנון משימה' })).toHaveCount(0);
  const status = await page.evaluate(async () => {
    const csrf = /(?:^|;\s*)ao_csrf=([^;]+)/.exec(document.cookie)?.[1] ?? '';
    const r = await fetch('/api/v1/missions', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: '{}',
    });
    return r.status;
  });
  expect(status).toBe(403);
});

async function draftMission(page: Page, name: string, startLocal: string) {
  await page.goto('/missions/new');
  await page.getByTestId('mission-name').fill(name);
  await page.getByTestId('mission-count').fill('2');
  await page.getByText('חד-מנועי', { exact: true }).click();
  await page.getByTestId('mission-start').fill(startLocal);
  await page.getByTestId('mission-duration').fill('2');
  await page.getByTestId('mission-base').click();
  await page.getByRole('option', { name: 'בסיס צפון' }).click();
  await page.getByTestId('save-draft-btn').click();
  await expect(page.getByTestId('mission-status')).toHaveText('טיוטה');
}

test('mission draft can be edited and re-saved', async ({ page }) => {
  await login(page, 'planner');
  await draftMission(page, 'טיוטת עריכה', '2026-01-16T09:00');
  await page.getByTestId('edit-mission-btn').click();
  await expect(page.getByTestId('mission-name')).toHaveValue('טיוטת עריכה');
  await page.getByTestId('mission-duration').fill('1:30');
  await page.getByTestId('save-draft-btn').click();
  await expect(page.getByTestId('mission-title')).toHaveText('טיוטת עריכה');
  await expect(page.getByText('1:30').first()).toBeVisible();
});

test('multi-mission planning never shares an aircraft between overlapping missions', async ({ page }) => {
  await login(page, 'planner');
  await draftMission(page, 'אימון בוקר א', '2026-01-17T09:00');
  await draftMission(page, 'אימון בוקר ב', '2026-01-17T10:00');
  await page.getByRole('link', { name: 'תכנון רב-משימתי' }).click();
  await page.getByTestId('horizon-compute').click();
  const result = page.getByTestId('horizon-result');
  await expect(result).toBeVisible();
  const tailsOf = (name: string) => result.locator('tr[data-mission]')
    .filter({ has: page.locator('td:first-child', { hasText: new RegExp(`^${name}$`) }) }).locator('bdi')
    .evaluateAll((els) => els.map((e) => e.textContent ?? ''));
  const a = await tailsOf('אימון בוקר א');
  const b = await tailsOf('אימון בוקר ב');
  expect(a.length + b.length).toBeGreaterThan(0);
  expect(a.filter((tail) => b.includes(tail))).toEqual([]); // overlapping windows ⇒ disjoint aircraft
  await page.getByTestId('horizon-apply').click();
  await page.getByTestId('horizon-confirm').click();
  await expect(page.getByText('התכנית שובצה')).toBeVisible();
});

test('fleet apron shows every aircraft in 3D and opens it on click', async ({ page }) => {
  await login(page, 'ops');
  await page.goto('/fleet');
  await page.getByTestId('fleet-3d-btn').click();
  await expect(page.getByTestId('fleet-apron')).toBeVisible();
  await expect(page.locator('button.ao-hotspot[data-tail]')).toHaveCount(24);
  await expect(page.locator('button.ao-hotspot[data-tail="4X-J04"]')).toHaveAttribute('data-status', 'EXPIRED');
  await page.locator('button.ao-hotspot[data-tail="4X-J04"]').click();
  await expect(page).toHaveURL(/\/aircraft\/4X-J04$/);
});
