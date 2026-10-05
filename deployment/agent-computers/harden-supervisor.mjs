/*
 * Zelo hardening over pinned CopilotKit/OpenBot.
 * OpenDots/OpenBot MIT notices are retained beside this file.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

function replaceExactly(source, before, after, label) {
  if (source.split(before).length !== 2)
    throw new Error(
      `Pinned OpenBot ${label} contract changed; review before building.`,
    );
  return source.replace(before, after);
}

export function hardenSupervisorEnvironment(source) {
  source = replaceExactly(
    source,
    `  const passthrough = Object.entries(env).filter(([key]) =>
    key.startsWith("EGRESS_PROXY"),
  );`,
    `  const passthrough = Object.entries(env).filter(([key]) =>
    key.startsWith("EGRESS_PROXY"),
  );
  if (passthrough.length)
    throw new Error("Raw EGRESS_PROXY settings are disabled by Zelo.");`,
    'environment proxy passthrough',
  );

  source = replaceExactly(
    source,
    '  const computerToken = env.COMPUTER_TOKEN?.trim() || undefined;',
    `  const master = env.COMPUTER_TOKEN?.trim();
  if (!master || master.length < 24)
    throw new Error("COMPUTER_TOKEN must contain at least 24 characters.");
  const computerToken = createHmac("sha256", master)
    .update("zelo-computer:" + botId)
    .digest("hex");

  const egressProxyHost =
    env.ZELO_COMPUTER_EGRESS_PROXY_HOST?.trim() || undefined;
  const egressProxyPort =
    env.ZELO_COMPUTER_EGRESS_PROXY_PORT?.trim() || "4390";
  let egressProxyDefault;
  if (egressProxyHost) {
    if (
      !/^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}$/.test(egressProxyHost) ||
      !/^[0-9]+$/.test(egressProxyPort)
    )
      throw new Error("Zelo egress proxy host/port are invalid.");
    const port = Number.parseInt(egressProxyPort, 10);
    if (!Number.isSafeInteger(port) || port < 1024 || port > 65535)
      throw new Error("Zelo egress proxy port is invalid.");
    const proxy = new URL(\`http://\${egressProxyHost}:\${port}\`);
    if (proxy.hostname !== egressProxyHost || proxy.port !== String(port))
      throw new Error("Zelo egress proxy authority is not canonical.");
    proxy.username = botId;
    proxy.password = createHmac("sha256", master)
      .update("zelo-egress:" + botId)
      .digest("hex");
    egressProxyDefault = proxy.toString();
  }`,
    'environment token',
  );

  source = replaceExactly(
    source,
    '    ...passthrough.map(([key, value]) => `${key}=${value ?? ""}`),',
    `    ...(egressProxyDefault
      ? [\`EGRESS_PROXY_DEFAULT=\${egressProxyDefault}\`]
      : []),`,
    'environment return',
  );
  return `import { createHmac } from "node:crypto";\n${source}`;
}

export function hardenSupervisorDocker(source) {
  source = replaceExactly(
    source,
    `export async function listOwned(): Promise<ComputerState[]> {
  try {
    const containers = (
      await docker.listContainers({
        all: true,
        filters: { label: [\`\${OWNER_LABEL}=true\`] },
      })
    ).filter((container) => ours(container.Labels));
    return containers.map((container) => ({
      botId: container.Labels?.[BOT_LABEL] ?? "unknown",
      container: (container.Names?.[0] ?? "").replace(/^\\//, ""),
      status: container.State,
      ...(container.Created
        ? { startedAt: new Date(container.Created * 1000).toISOString() }
        : {}),
      ...(portOf(container.Ports) ? { port: portOf(container.Ports) } : {}),
    }));
  } catch (error) {
    throw new DockerUnavailableError(String(error));
  }
}`,
    `export async function listOwned(network?: string): Promise<ComputerState[]> {
  try {
    const containers = (
      await docker.listContainers({
        all: true,
        filters: { label: [\`\${OWNER_LABEL}=true\`] },
      })
    ).filter((container) => ours(container.Labels));
    return containers.map((container) => {
      const port = portOf(container.Ports);
      const address = network
        ? container.NetworkSettings?.Networks?.[network]?.IPAddress
        : undefined;
      return {
        botId: container.Labels?.[BOT_LABEL] ?? "unknown",
        container: (container.Names?.[0] ?? "").replace(/^\\//, ""),
        status: container.State,
        ...(container.Created
          ? { startedAt: new Date(container.Created * 1000).toISOString() }
          : {}),
        ...(!network && port ? { port } : {}),
        ...(network
          ? address
            ? { url: \`http://\${address}:4100\` }
            : {}
          : port
            ? { url: \`http://127.0.0.1:\${port}\` }
            : {}),
      };
    });
  } catch (error) {
    throw new DockerUnavailableError(String(error));
  }
}`,
    'docker list',
  );

  source = replaceExactly(
    source,
    `async function inspectOwned(names: ComputerNames): Promise<{
  status: string;
  port?: number;
  image?: string;
  startedAt?: string;
  token?: string;
} | null> {`,
    `async function inspectOwned(
  names: ComputerNames,
  network?: string,
): Promise<{
  status: string;
  port?: number;
  address?: string;
  image?: string;
  startedAt?: string;
  token?: string;
} | null> {`,
    'docker inspect signature',
  );

  source = replaceExactly(
    source,
    `    const port = parseHostPort(published);
    return {
      status: info.State?.Status ?? "unknown",
      ...(port !== undefined ? { port } : {}),`,
    `    const port = parseHostPort(published);
    const address = network
      ? info.NetworkSettings?.Networks?.[network]?.IPAddress
      : undefined;
    return {
      status: info.State?.Status ?? "unknown",
      ...(port !== undefined ? { port } : {}),
      ...(typeof address === "string" && address ? { address } : {}),`,
    'docker inspect address',
  );

  source = replaceExactly(
    source,
    '    let existing = await inspectOwned(names);',
    '    let existing = await inspectOwned(names, options.network);',
    'docker ensure initial inspect',
  );
  source = replaceExactly(
    source,
    '        if (!(await inspectOwned(names))) {',
    '        if (!(await inspectOwned(names, options.network))) {',
    'docker ensure conflict inspect',
  );
  source = replaceExactly(
    source,
    `    const settled = await inspectOwned(names);
    await waitUntilAnswering(names.container, options.readyTimeoutMs);

    return {`,
    `    const settled = await inspectOwned(names, options.network);
    await waitUntilAnswering(names.container, options.readyTimeoutMs);
    if (options.network && !settled?.address)
      throw new DockerUnavailableError(
        "The configured computer network did not provide a control address.",
      );

    return {`,
    'docker ensure settled inspect',
  );

  source = replaceExactly(
    source,
    `      // How to reach it. A name on a shared network, a host port otherwise, the caller does not have
      // to know which arrangement it is in.
      ...(options.network
        ? { url: \`http://\${names.container}:4100\` }
        : settled?.port
          ? { url: \`http://127.0.0.1:\${settled.port}\` }
          : {}),`,
    `      // Host-mode Zelo Core uses a published loopback port in legacy mode,
      // or the exact private address Docker assigned on the configured internal network.
      ...(options.network
        ? settled?.address
          ? { url: \`http://\${settled.address}:4100\` }
          : {}
        : settled?.port
          ? { url: \`http://127.0.0.1:\${settled.port}\` }
          : {}),`,
    'docker ensure URL',
  );
  return source;
}

export function hardenSupervisorIndex(source) {
  return replaceExactly(
    source,
    '    return context.json({ computers: await listOwned() });',
    '    return context.json({ computers: await listOwned(network) });',
    'index list network binding',
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const environmentPath = process.argv[2];
  const dockerPath = process.argv[3];
  const indexPath = process.argv[4];
  if (!environmentPath || !dockerPath || !indexPath)
    throw new Error(
      'Pass the pinned supervisor environment.ts, docker.ts and index.ts paths.',
    );
  await writeFile(
    environmentPath,
    hardenSupervisorEnvironment(await readFile(environmentPath, 'utf8')),
  );
  await writeFile(
    dockerPath,
    hardenSupervisorDocker(await readFile(dockerPath, 'utf8')),
  );
  await writeFile(
    indexPath,
    hardenSupervisorIndex(await readFile(indexPath, 'utf8')),
  );
}
