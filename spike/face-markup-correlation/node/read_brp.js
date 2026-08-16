// Phase 1 spike -- occt-import-js side (Node).
//
// Reads each .brp produced by the FreeCAD-side script with the library's
// documented Node entry point (ReadBrepFile), and writes, per shape, a JSON
// with one entry per element of the returned `brep_faces` array, IN THAT
// ARRAY'S ORDER (index 0, 1, 2, ...): an approximate centroid computed by
// averaging the triangle vertex positions of that face's triangle range.
//
// `brep_faces[i]` is an inclusive triangle-index range {first, last} into the
// mesh's triangle index buffer; each triangle is 3 consecutive entries of
// `index.array` indexing into `attributes.position.array` (xyz triplets).
//
// Usage: node read_brp.js [shape...]   (default: all shapes in ../shapes)

const fs = require("fs");
const path = require("path");
const occtimportjs = require("occt-import-js")();

const HERE = __dirname;
const SHAPE_DIR = path.join(HERE, "..", "shapes");
const OUT_DIR = path.join(HERE, "..", "out");
const VERSION = "0.0.23"; // pinned; see package.json

function triangleCentroid(v, idx, triStart) {
  // triStart = index into the index buffer of the first vertex index of the
  // triangle; v = xyz float buffer (attributes.position.array)
  const i0 = idx[triStart] * 3;
  const i1 = idx[triStart + 1] * 3;
  const i2 = idx[triStart + 2] * 3;
  return [
    (v[i0] + v[i1] + v[i2]) / 3,
    (v[i0 + 1] + v[i1 + 1] + v[i2 + 1]) / 3,
    (v[i0 + 2] + v[i1 + 2] + v[i2 + 2]) / 3,
  ];
}

function triangleArea(v, idx, triStart) {
  const i0 = idx[triStart] * 3;
  const i1 = idx[triStart + 1] * 3;
  const i2 = idx[triStart + 2] * 3;
  const ax = v[i1] - v[i0], ay = v[i1 + 1] - v[i0 + 1], az = v[i1 + 2] - v[i0 + 2];
  const bx = v[i2] - v[i0], by = v[i2 + 1] - v[i0 + 1], bz = v[i2 + 2] - v[i0 + 2];
  const cx = ay * bz - az * by, cy = az * bx - ax * bz, cz = ax * by - ay * bx;
  return 0.5 * Math.sqrt(cx * cx + cy * cy + cz * cz);
}

function faceRecords(mesh) {
  const v = mesh.attributes.position.array;
  const idx = mesh.index.array;
  const triCount = idx.length / 3;
  const records = [];
  let covered = 0;
  for (const range of mesh.brep_faces) {
    const n = range.last - range.first + 1; // inclusive range
    const c = [0, 0, 0];
    let area = 0;
    for (let t = range.first; t <= range.last; t++) {
      const tc = triangleCentroid(v, idx, t * 3);
      c[0] += tc[0];
      c[1] += tc[1];
      c[2] += tc[2];
      area += triangleArea(v, idx, t * 3);
    }
    covered += n;
    records.push({
      first: range.first,
      last: range.last,
      triangles: n,
      centroid: [c[0] / n, c[1] / n, c[2] / n],
      approx_area: area,
    });
  }
  if (covered !== triCount) {
    throw new Error(
      `brep_faces ranges cover ${covered} triangles but mesh has ${triCount}`
    );
  }
  return records;
}

function processShape(occt, name) {
  const brpPath = path.join(SHAPE_DIR, name + ".brp");
  const content = fs.readFileSync(brpPath);
  const result = occt.ReadBrepFile(content, null);
  if (!result.success) {
    throw new Error(`ReadBrepFile failed for ${name}: success=false`);
  }
  // One mesh per source solid; join their face lists in mesh order. For these
  // single-solid shapes there is exactly one mesh, but stay generic.
  const records = [];
  for (const mesh of result.meshes) {
    records.push(...faceRecords(mesh));
  }
  const payload = {
    shape: name,
    source: "occt-import-js",
    occt_import_js_version: VERSION,
    root: result.root,
    mesh_count: result.meshes.length,
    face_count: records.length,
    faces: records.map((r, i) => ({ index: i, ...r })),
  };
  const outPath = path.join(OUT_DIR, name + ".occt-import-js.json");
  fs.writeFileSync(outPath, JSON.stringify(payload, null, 2));
  console.log(
    `[occt-import-js] ${name}: ${records.length} brep_faces -> ${outPath}`
  );
}

const shapes = process.argv.slice(2);
const all = fs
  .readdirSync(SHAPE_DIR)
  .filter((f) => f.endsWith(".brp"))
  .map((f) => f.replace(/\.brp$/, ""));

occtimportjs.then((occt) => {
  try {
    for (const name of shapes.length ? shapes : all) {
      processShape(occt, name);
    }
    console.log("[occt-import-js] done");
  } catch (e) {
    console.error("[occt-import-js] ERROR:", e.message);
    process.exit(1);
  }
});
