import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

export const specification = JSON.parse(readFileSync(new URL('../../../site/data/commons-openapi.json', import.meta.url), 'utf8'));

// Response-contract checks for the schema vocabulary used in this document.
// This deliberately does not claim to be a general OpenAPI validator.
export function matches(value, schema, path = '$') {
  if (schema.$ref) matches(value, schema.$ref.slice(2).split('/').reduce((node, key) => node[key], specification), path);
  for (const child of schema.allOf || []) matches(value, child, path);
  if (schema.oneOf) assert.equal(schema.oneOf.filter(child => { try { matches(value, child, path); return true; } catch { return false; } }).length, 1, `${path}: oneOf`);
  if (schema.anyOf) assert.ok(schema.anyOf.some(child => { try { matches(value, child, path); return true; } catch { return false; } }), `${path}: anyOf`);
  if ('const' in schema) assert.deepEqual(value, schema.const, `${path}: const`);
  if (schema.enum) assert.ok(schema.enum.includes(value), `${path}: enum`);
  if (schema.type) {
    const actual = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    assert.ok(types.includes(actual) || (types.includes('integer') && Number.isInteger(value)), `${path}: type`);
  }
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    for (const field of schema.required || []) assert.ok(field in value, `${path}: missing ${field}`);
    if (schema.additionalProperties === false) for (const field of Object.keys(value)) assert.ok(field in schema.properties, `${path}: extra ${field}`);
    for (const [field, child] of Object.entries(schema.properties || {})) if (field in value) matches(value[field], child, `${path}.${field}`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined) assert.ok(value.length >= schema.minItems, path);
    if (schema.maxItems !== undefined) assert.ok(value.length <= schema.maxItems, path);
    if (schema.items) value.forEach((item, index) => matches(item, schema.items, `${path}[${index}]`));
  }
  if (typeof value === 'string') {
    if (schema.pattern) assert.match(value, new RegExp(schema.pattern, 'u'), path);
    if (schema.minLength !== undefined) assert.ok([...value].length >= schema.minLength, path);
    if (schema.maxLength !== undefined) assert.ok([...value].length <= schema.maxLength, path);
    if (schema.format === 'date-time') assert.ok(Number.isFinite(Date.parse(value)), path);
    if (schema.format === 'uri') assert.ok(new URL(value).protocol, path);
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined) assert.ok(value >= schema.minimum, path);
    if (schema.maximum !== undefined) assert.ok(value <= schema.maximum, path);
  }
}
