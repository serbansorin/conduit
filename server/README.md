# server

The server bootstrap shared by the CLI and the desktop app.

`server.Run(ctx, Options)` opens the database, loads routes and mappings,
syncs the model catalog, builds the router and listens. It reports the
address through `Options.OnReady` and shuts down cleanly on context
cancellation or a signal.

`Options` covers `Port`, `Bind`, `UseRandomPort`, `AsyncModelSync`,
`SkipModelSync` and `OnReady`. `Result` carries `Addr`, `BaseURL`,
`AdminURL` and `Token`.
