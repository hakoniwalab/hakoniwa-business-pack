import {
  PduManager,
  PduEncoding,
  WebSocketCommunicationService,
} from '/pdu-js/index.js';

const ROBOT = 'hako_city_world_job';
const PDU = 'message';
const MAX_WIRE_BYTES = 16 * 1024;

function canonicalize(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalize).join(',')}]`;
  }
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(
      (key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`,
    ).join(',')}}`;
  }
  return JSON.stringify(value);
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(canonicalize(value));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function safeJobId(value) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
    throw new Error('job_id must use only letters, digits, dot, underscore, and hyphen');
  }
  return value;
}

export class CityWorldPduClient {
  constructor({ statusPollMsec = 50 } = {}) {
    this.manager = new PduManager({ wire_version: 'v2', pdu_encoding: PduEncoding.HAKO });
    this.transport = new WebSocketCommunicationService('v2');
    this.statusPollMsec = statusPollMsec;
    this.sequence = 0;
    this.statusQueue = [];
  }

  async connect({ configUrl = '/pdu-config.json', websocketUrl = 'ws://127.0.0.1:54210' } = {}) {
    await this.manager.initialize(configUrl, this.transport);
    this.transport.register_data_event_handler(async (packet) => {
      if (packet.robot_name !== ROBOT || packet.channel_id !== 1) return;
      if (packet.body_data.byteLength > MAX_WIRE_BYTES) {
        this.statusQueue.push(new Error('City World status is too large'));
        return;
      }
      try {
        const message = JSON.parse(
          new TextDecoder('utf-8', { fatal: true }).decode(packet.body_data),
        );
        if (message.protocol !== 'hakoniwa.city-world-job' || message.kind !== 'status') {
          throw new Error('received an invalid City World status envelope');
        }
        this.statusQueue.push(message);
      } catch (error) {
        this.statusQueue.push(error);
      }
    });
    const started = await this.manager.start_service(websocketUrl);
    if (!started) throw new Error(`failed to connect to ${websocketUrl}`);
  }

  async close() {
    await this.manager.stop_service();
  }

  isConnected() {
    return this.manager.is_service_enabled();
  }

  async inspect({
    jobId, latitude, longitude, halfExtentNorthSouth, halfExtentEastWest,
    buildingPhysicsLevel,
  }) {
    const request = {
      schema_version: 1,
      selection: {
        center: { latitude: Number(latitude), longitude: Number(longitude) },
        half_extent_m: {
          north_south: Number(halfExtentNorthSouth),
          east_west: Number(halfExtentEastWest),
        },
      },
      profile: 'visual-physics-v1',
      year: 'latest',
      options: { building_physics_level: Number(buildingPhysicsLevel) },
    };
    const message = {
      schema_version: 1,
      protocol: 'hakoniwa.city-world-job',
      kind: 'command',
      type: 'INSPECT_SELECTION',
      job_id: safeJobId(jobId),
      sequence: ++this.sequence,
      source_host: 'city-world-browser',
      request_sha256: await sha256(request),
      request,
    };
    const bytes = new TextEncoder().encode(canonicalize(message));
    if (bytes.byteLength > MAX_WIRE_BYTES) throw new Error('City World message is too large');
    if (!await this.manager.flush_pdu_raw_data(ROBOT, PDU, bytes.buffer)) {
      throw new Error('failed to send INSPECT_SELECTION');
    }
    return message;
  }

  async generate({ jobId, request, inspection }) {
    const message = {
      schema_version: 1,
      protocol: 'hakoniwa.city-world-job',
      kind: 'command',
      type: 'GENERATE',
      job_id: safeJobId(jobId),
      sequence: ++this.sequence,
      source_host: 'city-world-browser',
      request_sha256: await sha256(request),
      inspection_sha256: await sha256(inspection),
      request,
    };
    const bytes = new TextEncoder().encode(canonicalize(message));
    if (bytes.byteLength > MAX_WIRE_BYTES) throw new Error('City World message is too large');
    if (!await this.manager.flush_pdu_raw_data(ROBOT, PDU, bytes.buffer)) {
      throw new Error('failed to send GENERATE');
    }
    return message;
  }

  async nextStatus(timeoutMsec = 60000) {
    const deadline = Date.now() + timeoutMsec;
    while (Date.now() < deadline) {
      if (this.statusQueue.length > 0) {
        const message = this.statusQueue.shift();
        if (message instanceof Error) throw message;
        return message;
      }
      await new Promise((resolve) => setTimeout(resolve, this.statusPollMsec));
    }
    throw new Error(`no City World status within ${timeoutMsec} ms`);
  }
}
