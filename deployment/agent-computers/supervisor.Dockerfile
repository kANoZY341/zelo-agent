# Adapted from CopilotKit/OpenDots c2569bb6a13a22e565cf3eb791c62267d06babb1
# deployment/computers/supervisor.Dockerfile under the MIT License.
# Source comes from the exact OpenBot revision in compose.agent-computers.yml.
FROM oven/bun:1.3.14-alpine
WORKDIR /app
COPY --from=openbot supervisor/package.json supervisor/bun.lock ./
RUN bun install --frozen-lockfile
COPY --from=openbot supervisor/src ./src
COPY deployment/agent-computers/LICENSE.openbot ./LICENSE.openbot
COPY deployment/agent-computers/LICENSE.opendots ./LICENSE.opendots
COPY deployment/agent-computers/harden-supervisor.mjs /tmp/harden-supervisor.mjs
RUN bun /tmp/harden-supervisor.mjs /app/src/environment.ts /app/src/docker.ts /app/src/index.ts && rm /tmp/harden-supervisor.mjs
EXPOSE 4300
CMD ["bun", "src/index.ts"]
