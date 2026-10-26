import { expect, test } from '@playwright/test'

test('creates a task and executes an approved local automation', async ({ page }) => {
  const browserErrors: string[] = []
  page.on('pageerror', error => browserErrors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: /Good (morning|afternoon|evening)/ })).toBeVisible()
  await page.getByRole('button', { name: 'Tasks & Reminders' }).click()
  await page.getByRole('button', { name: 'New task' }).last().click()
  await page.getByPlaceholder('e.g. Submit project outline').fill('Browser smoke task')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Browser smoke task')).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'Tasks & Reminders' }).click()
  await expect(page.getByText('Browser smoke task')).toBeVisible()

  await page.getByRole('button', { name: 'Automations', exact: true }).click()
  await page.getByLabel('Your request').fill('Read my open tasks and save a daily plan')
  await page.getByRole('button', { name: 'Propose plan' }).click()
  await expect(page.getByRole('heading', { name: 'Browser daily plan' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run now' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Approve exact plan' }).click()
  await page.getByRole('button', { name: 'Run now' }).click()
  await expect(page.getByText(/Succeeded/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('Verified result').first()).toBeVisible()

  const artifacts = await (await page.request.get('/api/automation/artifacts')).json() as { source_run_id: string }[]
  expect(artifacts).toHaveLength(1)
  const tasks = await (await page.request.get('/api/tasks')).json() as { title: string }[]
  expect(tasks.some(task => task.title === 'Browser smoke task')).toBe(true)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(page.getByRole('button', { name: 'Open menu' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.getByRole('button', { name: 'Open menu' }).click()
  await page.getByRole('button', { name: 'Tasks & Reminders' }).click()
  await expect(page.getByText('Browser smoke task')).toBeVisible()
  expect(browserErrors).toEqual([])
})
