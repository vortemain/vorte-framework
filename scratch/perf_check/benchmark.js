const { spawn } = require('child_process');
const autocannon = require('autocannon');
const http = require('http');

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForPort(port) {
  for (let i = 0; i < 30; i++) {
    try {
      await new Promise((resolve, reject) => {
        const req = http.get(`http://127.0.0.1:${port}/api/v1/hello`, (res) => {
          if (res.statusCode === 200) resolve();
          else reject();
        });
        req.on('error', reject);
        req.end();
      });
      return;
    } catch (err) {
      await delay(200);
    }
  }
  throw new Error(`Timeout waiting for port ${port}`);
}

async function runAutocannon(port) {
  return new Promise((resolve, reject) => {
    autocannon({
      url: `http://127.0.0.1:${port}/api/v1/hello`,
      connections: 100,
      duration: 10,
      pipelining: 1
    }, (err, result) => {
      if (err) reject(err);
      else resolve(result);
    });
  });
}

async function main() {
  const results = [];

  // --- 1. Benchmarking Vorte ---
  console.log('Starting Vorte Server...');
  const vorteProcess = spawn(
    'C:\\Projects\\vorte-framework\\.venv\\Scripts\\python.exe',
    ['vorte_server.py'],
    { stdio: 'ignore' }
  );
  try {
    await waitForPort(8000);
    console.log('Vorte Server started. Benchmarking...');
    const vorteResult = await runAutocannon(8000);
    results.push({
      name: 'Vorte (Rust Engine)',
      rps: vorteResult.requests.average,
      avgLatency: vorteResult.latency.average,
      p99Latency: vorteResult.latency.p99
    });
    console.log('Vorte Server benchmark finished.');
  } finally {
    vorteProcess.kill('SIGKILL');
  }

  await delay(1000);

  // --- 2. Benchmarking Node Native ---
  console.log('Starting Node Native Server...');
  const nodeNativeProcess = spawn('node', ['node_native.js'], { stdio: 'ignore' });
  try {
    await waitForPort(3000);
    console.log('Node Native Server started. Benchmarking...');
    const nodeNativeResult = await runAutocannon(3000);
    results.push({
      name: 'Node.js Native HTTP',
      rps: nodeNativeResult.requests.average,
      avgLatency: nodeNativeResult.latency.average,
      p99Latency: nodeNativeResult.latency.p99
    });
    console.log('Node Native Server benchmark finished.');
  } finally {
    nodeNativeProcess.kill('SIGKILL');
  }

  await delay(1000);

  // --- 3. Benchmarking Node Fastify ---
  console.log('Starting Node Fastify Server...');
  const nodeFastifyProcess = spawn('node', ['node_fastify.js'], { stdio: 'ignore' });
  try {
    await waitForPort(3000);
    console.log('Node Fastify Server started. Benchmarking...');
    const nodeFastifyResult = await runAutocannon(3000);
    results.push({
      name: 'Node.js Fastify Framework',
      rps: nodeFastifyResult.requests.average,
      avgLatency: nodeFastifyResult.latency.average,
      p99Latency: nodeFastifyResult.latency.p99
    });
    console.log('Node Fastify Server benchmark finished.');
  } finally {
    nodeFastifyProcess.kill('SIGKILL');
  }

  // Print results
  console.log('\n======================================================');
  console.log('                    BENCHMARK RESULTS');
  console.log('======================================================\n');
  console.log('| Framework/Server | Requests/sec | Avg Latency (ms) | P99 Latency (ms) |');
  console.log('| ---------------- | ------------ | ---------------- | ---------------- |');
  for (const r of results) {
    console.log(`| ${r.name.padEnd(28)} | ${r.rps.toFixed(1).padStart(12)} | ${r.avgLatency.toFixed(2).padStart(16)} | ${r.p99Latency.toFixed(2).padStart(16)} |`);
  }
}

main().catch(console.error);
