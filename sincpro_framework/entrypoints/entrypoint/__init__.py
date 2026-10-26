"""What a project writes: the exposure it declares on its use cases (`@rest`, `@rpc`, `@grpc`,
`@mcp`, `@queue`, `@internal`), the workers that run its loops (`Process`, `Poll`), and each
wire's gateway — the services (`Gateway`, `Catalog`, `ScalarExecution`) composed with the wire's
adapter (`rest`, `rpc`, `grpc`, `mcp`, `queue`, `fastapi`, `aws_lambda`)."""
