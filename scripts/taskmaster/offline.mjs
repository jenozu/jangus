// Defense in depth for this deliberately offline profile; not an OS security sandbox.
import net from "node:net";
import tls from "node:tls";
import http from "node:http";
import https from "node:https";
import http2 from "node:http2";
import dgram from "node:dgram";
import dns from "node:dns";
import { syncBuiltinESMExports } from "node:module";

function blocked() { throw new Error("Network access is disabled in the Jangus Taskmaster profile."); }
net.Socket.prototype.connect = blocked;
net.connect = net.createConnection = blocked;
net.Server.prototype.listen = blocked;
tls.connect = blocked;
http.request = http.get = https.request = https.get = blocked;
http2.connect = blocked;
dgram.createSocket = blocked;
dns.lookup = dns.resolve = blocked;
dns.promises.lookup = dns.promises.resolve = async () => blocked();
globalThis.fetch = async () => blocked();
globalThis.WebSocket = class { constructor() { blocked(); } };
syncBuiltinESMExports();
