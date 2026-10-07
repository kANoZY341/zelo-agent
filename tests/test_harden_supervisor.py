import shutil
import subprocess
import textwrap
from pathlib import Path


def test_generated_supervisor_environment_validates_agent_proxy_with_node(tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the generated-code regression."

    fixture = textwrap.dedent(
        """
        export function environment(env, botId) {
          const passthrough = Object.entries(env).filter(([key]) =>
            key.startsWith("EGRESS_PROXY"),
          );
          const computerToken = env.COMPUTER_TOKEN?.trim() || undefined;
          return [
            @@TOKEN@@,
            ...passthrough.map(([key, value]) => @@PAIR@@),
          ];
        }
        """
    )
    empty = chr(34) * 2
    fixture = fixture.replace(
        "@@TOKEN@@",
        chr(96) + "COMPUTER_TOKEN=$" + "{computerToken ?? " + empty + "}" + chr(96),
    )
    fixture = fixture.replace(
        "@@PAIR@@",
        chr(96) + "$" + "{key}=$" + "{value ?? " + empty + "}" + chr(96),
    )
    fixture_path = tmp_path / "environment.ts"
    generated_path = tmp_path / "environment.mjs"
    fixture_path.write_text(fixture, encoding="utf-8")

    driver = """
    import assert from "node:assert/strict";
    import { readFile, writeFile } from "node:fs/promises";
    import { createHmac } from "node:crypto";
    import { pathToFileURL } from "node:url";

    const [hardenerPath, fixturePath, generatedPath] = process.argv.slice(1);
    process.argv[1] = "";
    const { hardenSupervisorEnvironment } = await import(pathToFileURL(hardenerPath).href);
    const generated = hardenSupervisorEnvironment(await readFile(fixturePath, "utf8"));
    await writeFile(generatedPath, generated);
    const { environment } = await import(pathToFileURL(generatedPath).href);

    const base = {
      COMPUTER_TOKEN: "m".repeat(24),
      ZELO_COMPUTER_EGRESS_PROXY_HOST: "172.30.250.1",
      ZELO_COMPUTER_EGRESS_PROXY_PORT: "4391",
    };
    for (const host of ["172x30y250z1", "172-30-250-1"]) {
      assert.throws(
        () => environment({ ...base, ZELO_COMPUTER_EGRESS_PROXY_HOST: host }, "agent-1"),
        /proxy host[/]port are invalid/,
      );
    }

    const output = environment(base, "agent-1");
    const proxyLine = output.find((item) => item.startsWith("EGRESS_PROXY_DEFAULT="));
    assert.ok(proxyLine);
    const proxy = new URL(proxyLine.slice("EGRESS_PROXY_DEFAULT=".length));
    assert.equal(proxy.hostname, "172.30.250.1");
    assert.equal(proxy.port, "4391");
    assert.equal(proxy.username, "agent-1");
    assert.equal(
      proxy.password,
      createHmac("sha256", base.COMPUTER_TOKEN)
        .update("zelo-egress:agent-1")
        .digest("hex"),
    );
    """
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            driver,
            str(
                Path(__file__).resolve().parents[1]
                / "deployment/agent-computers/harden-supervisor.mjs"
            ),
            str(fixture_path),
            str(generated_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
