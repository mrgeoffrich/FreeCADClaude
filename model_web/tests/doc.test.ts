// SPDX-License-Identifier: LGPL-2.1-or-later
// The face-markup document: the round trip, and the shape of a mark -- a 3D
// face ordinal against a published BREP export, deliberately NOT the flat 2D
// pixel-coordinate schema of web/src/doc.ts (the plan and its scope document
// both call that conflation out by name).
import { describe, expect, it } from 'vitest';

import { DOC_VERSION, buildDoc, parseDoc, serializeDoc, type FaceMark } from '../src/doc';

const PUBLISH_ID = 'AbC123';

function mark(overrides: Partial<FaceMark> = {}): FaceMark {
  return {
    id: 'm1',
    object: 'Body',
    face_index: 3,
    color: null,
    note: 'widen this face',
    ...overrides,
  };
}

describe('buildDoc', () => {
  it('writes the shape from the design doc', () => {
    const doc = buildDoc({
      publishId: PUBLISH_ID,
      marks: [mark()],
      caption: 'slot too narrow',
    });
    expect(doc.version).toBe(DOC_VERSION);
    expect(doc.source).toEqual({ publish_id: PUBLISH_ID });
    expect(doc.marks).toEqual([
      { id: 'm1', object: 'Body', face_index: 3, color: null, note: 'widen this face' },
    ]);
    expect(doc.caption).toBe('slot too narrow');
  });

  it('supports several independent one-face marks', () => {
    // Multi-face grouping (one structured mark spanning several faces) is a
    // non-goal; several independent marks in one document is exactly what
    // the array is for.
    const doc = buildDoc({
      publishId: PUBLISH_ID,
      marks: [mark({ id: 'm1', face_index: 3 }), mark({ id: 'm2', face_index: 11, note: '' })],
      caption: '',
    });
    expect(doc.marks.map((m) => m.face_index)).toEqual([3, 11]);
  });

  it('keeps color reserved and always null in v1', () => {
    // A future paint-mode UI is additive to the same pick-resolution code;
    // the field exists now so that phase is a schema no-change.
    const doc = buildDoc({ publishId: PUBLISH_ID, marks: [mark({ color: '#ff0000' })], caption: '' });
    expect(doc.marks[0]?.color).toBeNull();
  });

  it('serializes exactly the five fields of the schema', () => {
    const doc = buildDoc({ publishId: PUBLISH_ID, marks: [], caption: '' });
    expect(Object.keys(doc).sort()).toEqual(['caption', 'marks', 'source', 'version']);
    expect(Object.keys(doc.marks.length ? doc.marks[0]! : mark()).sort()).toEqual([
      'color',
      'face_index',
      'id',
      'note',
      'object',
    ]);
  });
});

describe('round trip', () => {
  it('survives serialize -> parse unchanged', () => {
    const doc = buildDoc({
      publishId: PUBLISH_ID,
      marks: [mark({ note: 'widen this face' }), mark({ id: 'm2', face_index: 11 })],
      caption: 'hi',
    });
    expect(parseDoc(serializeDoc(doc))).toEqual(doc);
  });

  it('reads a document with no version as version 1', () => {
    // Nothing older than the schema exists, so a missing field is a likelier
    // bug than a time traveller.
    const parsed = parseDoc(
      JSON.stringify({ source: { publish_id: PUBLISH_ID }, marks: [], caption: '' }),
    );
    expect(parsed?.version).toBe(1);
  });

  it('refuses a document from a newer schema outright', () => {
    // Half-understanding a face reference is worse than not reading it: the
    // fields we recognise might mean something different in that version.
    expect(parseDoc(JSON.stringify({ version: DOC_VERSION + 1, source: { publish_id: 'x' }, marks: [] }))).toBeNull();
    expect(parseDoc(JSON.stringify({ version: 0, source: { publish_id: 'x' }, marks: [] }))).toBeNull();
    expect(parseDoc(JSON.stringify({ version: '1', source: { publish_id: 'x' }, marks: [] }))).toBeNull();
  });

  it('requires the source publish id', () => {
    // source.publish_id is load-bearing: it is how a later phase resolves an
    // uploaded mark back to the live FreeCAD object. A document without it
    // cannot be resolved, so it is not a document we understand.
    expect(parseDoc(JSON.stringify({ version: 1, marks: [], caption: '' }))).toBeNull();
    expect(parseDoc(JSON.stringify({ version: 1, source: {}, marks: [], caption: '' }))).toBeNull();
    expect(parseDoc(JSON.stringify({ version: 1, source: { publish_id: '' }, marks: [], caption: '' }))).toBeNull();
  });

  it('drops a malformed mark, keeping the ones it understands', () => {
    // Adding a kind of mark must not make an older reader throw the whole
    // document away.
    const parsed = parseDoc(
      JSON.stringify({
        version: 1,
        source: { publish_id: PUBLISH_ID },
        marks: [
          { id: 'm1', object: 'Body', face_index: 0, color: null, note: 'ok' },
          { id: '', object: 'Body', face_index: 0 }, // empty id
          { id: 'm2', object: 'Body', face_index: -1 }, // negative ordinal
          { id: 'm3', object: 'Body', face_index: 1.5 }, // not an integer
          { id: 'm4', face_index: 0 }, // no object
          { object: 'Body', face_index: 0 }, // no id
          'not a mark at all',
        ],
        caption: '',
      }),
    );
    expect(parsed?.marks.map((m) => m.id)).toEqual(['m1']);
  });

  it('reads anything unparseable as "not a document"', () => {
    expect(parseDoc('')).toBeNull();
    expect(parseDoc('{')).toBeNull();
    expect(parseDoc('null')).toBeNull();
    expect(parseDoc('"a string"')).toBeNull();
    expect(parseDoc('42')).toBeNull();
    expect(parseDoc('[]')).toBeNull();
  });

  it('defaults a missing mark note to an empty string', () => {
    const parsed = parseDoc(
      JSON.stringify({
        version: 1,
        source: { publish_id: PUBLISH_ID },
        marks: [{ id: 'm1', object: 'Body', face_index: 2 }],
        caption: '',
      }),
    );
    expect(parsed?.marks[0]).toEqual({
      id: 'm1',
      object: 'Body',
      face_index: 2,
      color: null,
      note: '',
    });
  });
});
