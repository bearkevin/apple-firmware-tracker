// 按 Cron Trigger 调用 GitHub workflow_dispatch，替代 GitHub Actions 自带的 schedule
// （后者在负载高时经常延迟或丢弃，实际每天只执行约 4 次）。

async function dispatchWorkflow(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "apple-firmware-tracker-cron",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.GITHUB_REF }),
  });

  // 成功时 GitHub 返回 204（新版 API 可能返回 200）；其余状态抛错，让 Cron 事件记录为失败
  if (response.status !== 204 && response.status !== 200) {
    const body = await response.text();
    throw new Error(`workflow_dispatch failed: ${response.status} ${body}`);
  }
}

export default {
  async scheduled(controller, env, ctx) {
    if (!env.GITHUB_TOKEN) {
      throw new Error("GITHUB_TOKEN secret is not set");
    }
    await dispatchWorkflow(env);
    console.log(`Dispatched ${env.GITHUB_WORKFLOW} on ${env.GITHUB_REF} (cron: ${controller.cron})`);
  },
};
