# LivaChat API Schema

**JSON Schema definitions for the LivaChat WebSocket protocol.**

Synchronized with `docs/ws-protocol-design.md` v1.3.

## Structure

```
packages/api-schema/
├── common.json              # Shared $defs: IDs, numeric types, enums, wrapper
├── c2s/                     # Client → Server messages
│   ├── session-start.json   #   Begin/resume session
│   ├── session-end.json     #   End session
│   ├── session-change.json  #   Pipeline mode switch + config pseudo-mode (avdetector)
│   ├── audio.json           #   Opus audio frame (20/40ms)
│   ├── video.json           #   JPEG video frame (~1fps, optional text turn)
│   ├── interrupt.json       #   Interrupt AI output
│   ├── ping.json            #   Heartbeat
│   ├── pong.json            #   Heartbeat reply
│   └── message.json         #   Discriminated union (anyOf)
├── s2c/                     # Server → Client messages
│   ├── session-ready.json   #   Session established
│   ├── session-ended.json   #   Session terminated
│   ├── session-changed.json #   Mode/config change confirmed
│   ├── transcription.json   #   ASR result (interim/final)
│   ├── ai-text.json         #   LLM streaming text (llm/agent source)
│   ├── job-status.json      #   Background job status snapshot
│   ├── ai-audio.json        #   TTS Opus audio chunk
│   ├── ai-video-ack.json    #   Video frame acknowledgement
│   ├── status.json          #   State machine transition
│   ├── error.json           #   Error notification
│   ├── ping.json            #   Heartbeat
│   ├── pong.json            #   Heartbeat reply
│   └── message.json         #   Discriminated union (anyOf)
└── message.json             #   Full protocol union (all 20 types)
```

## Usage

### Validate a single message type

```js
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import audioSchema from './c2s/audio.json';
import commonSchema from './common.json';

const ajv = new Ajv2020({ allErrors: true });
addFormats(ajv);
ajv.addSchema(commonSchema);              // shared $defs
ajv.addSchema(audioSchema);

const valid = ajv.validate(
  'https://livachat.dev/schemas/c2s/audio.json',
  messageData
);
```

### Validate any message (discriminated union)

```js
import messageSchema from './message.json';

ajv.addSchema(messageSchema);

const valid = ajv.validate(
  'https://livachat.dev/schemas/message.json',
  unknownMessage
);
```

### Validate C2S or S2C only

```js
import c2sUnion from './c2s/message.json';
import s2cUnion from './s2c/message.json';

ajv.addSchema(c2sUnion);
ajv.addSchema(s2cUnion);

const isC2S = ajv.validate('https://livachat.dev/schemas/c2s/message.json', msg);
```

## Versioning

Schema versions track the protocol document version. Breaking changes require:
1. Update `docs/ws-protocol-design.md`
2. Update affected `.json` schemas (examples, constraints, or new types)
3. Bump `$id` URL version segment
4. CI: run `npx ajv compile -s packages/api-schema/message.json -r 'packages/api-schema/**/*.json' --spec=draft2020`
