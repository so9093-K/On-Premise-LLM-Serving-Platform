const expectedNode = 'v24.20.0';
const expectedNpm = 'npm/11.19.0';

if (process.version !== expectedNode) {
  console.error(`Control Plane requires Node ${expectedNode.slice(1)}, got ${process.version}`);
  process.exit(2);
}

const userAgent = process.env.npm_config_user_agent ?? '';
if (!userAgent.startsWith(expectedNpm)) {
  console.error(`Control Plane requires ${expectedNpm}, got ${userAgent || 'unknown npm'}`);
  process.exit(2);
}
