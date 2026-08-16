// SPDX-License-Identifier: LGPL-2.1-or-later
// The wire-format guards: /api/latest and the SSE published event, parsed
// defensively. Everything here is pure parsing of the JSON shapes
// model_server.py actually emits (see its `_public_record`), and the one
// property under test is that a malformed or absent payload reads as
// "nothing loaded" -- a state the UI already renders -- never a thrown
// error.
//
// The WebGL render and the worker's real ReadBrepFile call are NOT tested
// here: they are a manual browser check, the same carve-out
// gcode_web/VENDORED.md takes for its own Viewer.tsx. A mock-heavy test of
// either would prove nothing about the real thing.
import { describe, expect, it } from 'vitest';
import {
  parseLatest,
  parseModelObject,
  parsePublishedEventData,
  parsePublishedModel,
} from '../src/api';

/** A publish exactly as model_server._public_record emits it. */
function record(overrides: Record<string, unknown> = {}) {
  return {
    id: 'AbC123',
    objects: {
      Body: {
        url: '/api/mesh/AbC123/Body.brp',
        shape_hash: '6b9f0a',
        faces: { '0': { centroid: [1, 2, 3], normal: [0, 0, 1], area: 4.5 } },
      },
    },
    published_at: 1750000000.5,
    ...overrides,
  };
}

describe('parseModelObject', () => {
  it('accepts a full entry', () => {
    const entry = parseModelObject(record().objects.Body);
    expect(entry).not.toBeNull();
    expect(entry!.url).toBe('/api/mesh/AbC123/Body.brp');
    expect(entry!.shape_hash).toBe('6b9f0a');
    expect(entry!.faces).toEqual({ '0': { centroid: [1, 2, 3], normal: [0, 0, 1], area: 4.5 } });
  });

  it('accepts an entry with only a url, defaulting the rest', () => {
    const entry = parseModelObject({ url: '/api/mesh/x/Body.brp' });
    expect(entry).toEqual({ url: '/api/mesh/x/Body.brp', shape_hash: '', faces: {} });
  });

  it('rejects null, non-objects and url-less entries', () => {
    expect(parseModelObject(null)).toBeNull();
    expect(parseModelObject(42)).toBeNull();
    expect(parseModelObject('url')).toBeNull();
    expect(parseModelObject({})).toBeNull();
    expect(parseModelObject({ url: '' })).toBeNull();
    expect(parseModelObject({ url: 7 })).toBeNull();
    expect(parseModelObject({ url: '/api/mesh/x/Body.brp', shape_hash: 5 })).toEqual({
      url: '/api/mesh/x/Body.brp',
      shape_hash: '',
      faces: {},
    });
  });
});

describe('parsePublishedModel', () => {
  it('parses a full record', () => {
    const published = parsePublishedModel(record());
    expect(published).not.toBeNull();
    expect(published!.id).toBe('AbC123');
    expect(Object.keys(published!.objects)).toEqual(['Body']);
    expect(published!.objects.Body.url).toBe('/api/mesh/AbC123/Body.brp');
    expect(published!.published_at).toBe(1750000000.5);
  });

  it('defaults a missing published_at to 0', () => {
    const published = parsePublishedModel(record({ published_at: undefined }));
    expect(published!.published_at).toBe(0);
  });

  it('returns null for absent or malformed payloads, never throws', () => {
    expect(parsePublishedModel(null)).toBeNull();
    expect(parsePublishedModel(undefined)).toBeNull();
    expect(parsePublishedModel(42)).toBeNull();
    expect(parsePublishedModel('a string')).toBeNull();
    expect(parsePublishedModel([])).toBeNull();
    expect(parsePublishedModel({})).toBeNull();
    expect(parsePublishedModel({ id: '' })).toBeNull();
    expect(parsePublishedModel({ id: 7 })).toBeNull();
    expect(parsePublishedModel({ id: 'x' })).toBeNull(); // no objects
    expect(parsePublishedModel({ id: 'x', objects: 'not an object' })).toBeNull();
    expect(parsePublishedModel({ id: 'x', objects: [] })).toBeNull();
    expect(parsePublishedModel({ id: 'x', objects: {} })).toBeNull();
  });

  it('drops malformed object entries but keeps the publish when one survives', () => {
    const published = parsePublishedModel({
      id: 'x',
      objects: {
        Good: { url: '/api/mesh/x/Good.brp' },
        Bad: null,
        Worse: { url: '' },
      },
    });
    expect(published).not.toBeNull();
    expect(Object.keys(published!.objects)).toEqual(['Good']);
  });

  it('treats a publish whose entries all fail as no publish', () => {
    expect(
      parsePublishedModel({ id: 'x', objects: { A: { url: '' }, B: null } }),
    ).toBeNull();
  });
});

describe('parseLatest', () => {
  it('parses the /api/latest envelope', () => {
    const published = parseLatest({ published: record() });
    expect(published!.id).toBe('AbC123');
  });

  it('reads {"published": null} as nothing loaded', () => {
    expect(parseLatest({ published: null })).toBeNull();
  });

  it('returns null for malformed envelopes, never throws', () => {
    expect(parseLatest(null)).toBeNull();
    expect(parseLatest(undefined)).toBeNull();
    expect(parseLatest('garbage')).toBeNull();
    expect(parseLatest([])).toBeNull();
    expect(parseLatest({})).toBeNull();
    expect(parseLatest({ published: 'nope' })).toBeNull();
    expect(parseLatest({ published: { id: 'x' } })).toBeNull(); // no objects
  });
});

describe('parsePublishedEventData', () => {
  it('parses a published SSE event payload', () => {
    const published = parsePublishedEventData(JSON.stringify(record()));
    expect(published).not.toBeNull();
    expect(published!.id).toBe('AbC123');
    expect(published!.objects.Body.shape_hash).toBe('6b9f0a');
  });

  it('reads a malformed event as nothing loaded without breaking the stream', () => {
    expect(parsePublishedEventData('not json {')).toBeNull();
    expect(parsePublishedEventData('42')).toBeNull();
    expect(parsePublishedEventData('"a string"')).toBeNull();
    expect(parsePublishedEventData('[]')).toBeNull();
    expect(parsePublishedEventData('null')).toBeNull();
    expect(parsePublishedEventData(42)).toBeNull();
    expect(parsePublishedEventData(null)).toBeNull();
    expect(parsePublishedEventData(undefined)).toBeNull();
    expect(parsePublishedEventData({})).toBeNull();
  });

  it('parses an event whose object lacks shape_hash and faces', () => {
    const published = parsePublishedEventData(
      JSON.stringify({ id: 'y', objects: { Pad: { url: '/api/mesh/y/Pad.brp' } } }),
    );
    expect(published!.objects.Pad.shape_hash).toBe('');
    expect(published!.objects.Pad.faces).toEqual({});
  });
});
