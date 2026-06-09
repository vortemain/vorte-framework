const fastify = require('fastify')();
fastify.get('/api/v1/hello', async (request, reply) => {
  return { message: "Welcome to Vorte!" };
});
fastify.listen({ port: 3000 }, (err, address) => {
  if (err) throw err;
  console.log(`Node Fastify server listening on ${address}`);
});
