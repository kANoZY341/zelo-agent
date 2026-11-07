import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";

const tests = readdirSync(new URL("../tests/", import.meta.url), {
  withFileTypes: true,
})
  .filter((entry) => entry.isFile() && /\.test\.[cm]?[jt]sx?$/.test(entry.name))
  .map((entry) => `tests/${entry.name}`)
  .sort();

if (tests.length === 0) {
  throw new Error("No Workspace test files found");
}

const result = spawnSync(process.execPath, ["--import", "tsx", "--test", ...tests], {
  stdio: "inherit",
});

if (result.error) throw result.error;
process.exit(result.status ?? 1);
