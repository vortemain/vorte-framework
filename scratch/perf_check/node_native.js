const http = require('http');
const server = http.createServer((req, res) => {
  if (req.url === '/api/v1/hello' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ message: "Welcome to Vorte!" }));
  } else {
    res.writeHead(404);
    res.end();
  }
});
server.listen(3000, () => {
  console.log('Node Native server listening on port 3000');
});
