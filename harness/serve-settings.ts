// serve-settings.ts — tiny local server for settings-editor.html
//
//   bun serve-settings.ts        # serves http://127.0.0.1:8437 and opens it
//
// GET  /              → settings-editor.html
// GET  /api/settings  → settings.json
// PUT  /api/settings  → validates JSON, backs up to settings.json.bak, writes
// GET  /api/skills    → global launch skills per harness:
//                       { pi: [~/.agents/skills/*], claude: [~/.claude/skills/*] }
//
// Localhost-only on purpose: this endpoint writes a file on disk.

const dir = import.meta.dir;
const settingsPath = `${dir}/settings.json`;
const htmlPath = `${dir}/settings-editor.html`;
const port = Number(process.env.PORT) || 8437; // PORT=… to run editors for two fleets at once

const server = Bun.serve({
	hostname: "127.0.0.1",
	port,
	async fetch(req) {
		const { pathname } = new URL(req.url);

		if (pathname === "/" && req.method === "GET") {
			return new Response(Bun.file(htmlPath), {
				headers: { "content-type": "text/html; charset=utf-8" },
			});
		}

		if (pathname === "/api/settings") {
			if (req.method === "GET") {
				return new Response(Bun.file(settingsPath), {
					headers: { "content-type": "application/json" },
				});
			}
			if (req.method === "PUT") {
				const text = await req.text();
				try {
					JSON.parse(text);
				} catch (e) {
					return new Response(`invalid JSON: ${(e as Error).message}`, { status: 400 });
				}
				const current = Bun.file(settingsPath);
				if (await current.exists()) {
					await Bun.write(`${settingsPath}.bak`, await current.text());
				}
				await Bun.write(settingsPath, text);
				return new Response("ok");
			}
		}

		if (pathname === "/api/skills" && req.method === "GET") {
			const { readdirSync, existsSync } = await import("node:fs");
			const scan = (dir: string) => {
				try {
					return readdirSync(dir, { withFileTypes: true })
						.filter((e) => e.isDirectory() && existsSync(`${dir}/${e.name}/SKILL.md`))
						.map((e) => e.name)
						.sort();
				} catch {
					return []; // no skills dir → empty list, editor keeps its snapshot
				}
			};
			return Response.json({
				pi: scan(`${process.env.HOME}/.agents/skills`),
				claude: scan(`${process.env.HOME}/.claude/skills`),
			});
		}

		return new Response("not found", { status: 404 });
	},
});

const url = `http://127.0.0.1:${server.port}`;
console.log(`settings editor → ${url}   (Ctrl-C to stop; last save backed up to settings.json.bak)`);
if (process.platform === "darwin" && !process.argv.includes("--no-open")) {
	Bun.spawn(["open", url]);
}
