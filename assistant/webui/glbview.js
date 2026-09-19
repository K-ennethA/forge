/* glbview.js — the Workspace's live model view.
 *
 * "It's almost like we should have a simplified blender window open within
 * forge."  This is that window: a .glb exported out of the running Blender,
 * drawn here, orbitable, with the rig's animation playable on it.
 *
 * Written from nothing rather than vendored from three.js, for the reason
 * every other file on this page is: Forge runs on a machine that is often
 * offline and always without a build step, so a viewer that needed a CDN — or
 * a bundler — would be the one panel that could not do its job.  What it does
 * is deliberately narrow:
 *
 *   * glTF 2.0 binary only (.glb), positions, normals and indices;
 *   * the node hierarchy, so a scene of several objects lands where Blender
 *     had it;
 *   * linear-blend skinning on the CPU, so a rigged character actually MOVES
 *     when its walk cycle is played rather than standing still while the
 *     armature waves about invisibly;
 *   * orbit, pan and zoom, because a model you cannot turn around is a
 *     picture, and there is already a panel full of pictures.
 *
 * What it is not: a renderer.  There are no materials, no textures and no
 * shadows — one clay shader, the same grey the Workbench previews use, lit
 * from the camera.  Looking at the SHAPE is the job; the renders folder is
 * where the pictures live.
 */
(function (global) {
  "use strict";

  // ------------------------------------------------------------- matrices --
  //
  // Column-major, the order WebGL wants, so nothing is transposed on its way
  // to a uniform.

  function identity(out) {
    out = out || new Float32Array(16);
    out[0] = 1; out[1] = 0; out[2] = 0; out[3] = 0;
    out[4] = 0; out[5] = 1; out[6] = 0; out[7] = 0;
    out[8] = 0; out[9] = 0; out[10] = 1; out[11] = 0;
    out[12] = 0; out[13] = 0; out[14] = 0; out[15] = 1;
    return out;
  }

  function multiply(out, a, b) {
    var a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3],
        a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7],
        a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11],
        a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];
    for (var i = 0; i < 4; i++) {
      var b0 = b[i * 4], b1 = b[i * 4 + 1], b2 = b[i * 4 + 2], b3 = b[i * 4 + 3];
      out[i * 4] = b0 * a00 + b1 * a10 + b2 * a20 + b3 * a30;
      out[i * 4 + 1] = b0 * a01 + b1 * a11 + b2 * a21 + b3 * a31;
      out[i * 4 + 2] = b0 * a02 + b1 * a12 + b2 * a22 + b3 * a32;
      out[i * 4 + 3] = b0 * a03 + b1 * a13 + b2 * a23 + b3 * a33;
    }
    return out;
  }

  function fromTRS(out, t, r, s) {
    var x = r[0], y = r[1], z = r[2], w = r[3];
    var x2 = x + x, y2 = y + y, z2 = z + z;
    var xx = x * x2, xy = x * y2, xz = x * z2;
    var yy = y * y2, yz = y * z2, zz = z * z2;
    var wx = w * x2, wy = w * y2, wz = w * z2;
    out[0] = (1 - (yy + zz)) * s[0];
    out[1] = (xy + wz) * s[0];
    out[2] = (xz - wy) * s[0];
    out[3] = 0;
    out[4] = (xy - wz) * s[1];
    out[5] = (1 - (xx + zz)) * s[1];
    out[6] = (yz + wx) * s[1];
    out[7] = 0;
    out[8] = (xz + wy) * s[2];
    out[9] = (yz - wx) * s[2];
    out[10] = (1 - (xx + yy)) * s[2];
    out[11] = 0;
    out[12] = t[0]; out[13] = t[1]; out[14] = t[2]; out[15] = 1;
    return out;
  }

  function perspective(out, fovy, aspect, near, far) {
    var f = 1.0 / Math.tan(fovy / 2);
    identity(out);
    out[0] = f / aspect;
    out[5] = f;
    out[10] = (far + near) / (near - far);
    out[11] = -1;
    out[14] = (2 * far * near) / (near - far);
    out[15] = 0;
    return out;
  }

  function lookAt(out, eye, centre, up) {
    var zx = eye[0] - centre[0], zy = eye[1] - centre[1], zz = eye[2] - centre[2];
    var len = Math.hypot(zx, zy, zz) || 1;
    zx /= len; zy /= len; zz /= len;
    var xx = up[1] * zz - up[2] * zy,
        xy = up[2] * zx - up[0] * zz,
        xz = up[0] * zy - up[1] * zx;
    len = Math.hypot(xx, xy, xz) || 1;
    xx /= len; xy /= len; xz /= len;
    var yx = zy * xz - zz * xy,
        yy = zz * xx - zx * xz,
        yz = zx * xy - zy * xx;
    out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
    out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
    out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
    out[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
    out[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
    out[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
    out[15] = 1;
    return out;
  }

  function slerp(out, a, b, t) {
    var ax = a[0], ay = a[1], az = a[2], aw = a[3];
    var bx = b[0], by = b[1], bz = b[2], bw = b[3];
    var cosom = ax * bx + ay * by + az * bz + aw * bw;
    if (cosom < 0) { cosom = -cosom; bx = -bx; by = -by; bz = -bz; bw = -bw; }
    var scale0, scale1;
    if (1 - cosom > 1e-6) {
      var omega = Math.acos(cosom), sinom = Math.sin(omega);
      scale0 = Math.sin((1 - t) * omega) / sinom;
      scale1 = Math.sin(t * omega) / sinom;
    } else {
      scale0 = 1 - t;
      scale1 = t;
    }
    out[0] = scale0 * ax + scale1 * bx;
    out[1] = scale0 * ay + scale1 * by;
    out[2] = scale0 * az + scale1 * bz;
    out[3] = scale0 * aw + scale1 * bw;
    return out;
  }

  // --------------------------------------------------------- reading a glb --

  var COMPONENTS = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 };
  var ARRAYS = {
    5120: Int8Array, 5121: Uint8Array, 5122: Int16Array,
    5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array
  };
  //: What a normalised integer attribute divides by (glTF 3.9.2.2). Joint
  //: weights arrive as unsigned bytes out of Blender more often than not.
  var NORMALISERS = { 5120: 127, 5121: 255, 5122: 32767, 5123: 65535 };

  /** Split a .glb into its JSON chunk and its binary chunk. */
  function parseGLB(buffer) {
    var view = new DataView(buffer);
    if (buffer.byteLength < 12) {
      throw new Error("That file is too short to be a .glb.");
    }
    if (view.getUint32(0, true) !== 0x46546c67) {   // "glTF"
      throw new Error("That file does not start with the glTF magic number.");
    }
    var version = view.getUint32(4, true);
    if (version !== 2) {
      throw new Error("This viewer reads glTF 2.0; that file says version " +
                      version + ".");
    }
    var offset = 12, json = null, bin = null;
    while (offset + 8 <= buffer.byteLength) {
      var length = view.getUint32(offset, true);
      var kind = view.getUint32(offset + 4, true);
      var start = offset + 8;
      if (start + length > buffer.byteLength) { break; }
      if (kind === 0x4e4f534a) {                    // "JSON"
        json = JSON.parse(new TextDecoder("utf-8")
          .decode(new Uint8Array(buffer, start, length)));
      } else if (kind === 0x004e4942) {             // "BIN"
        bin = new Uint8Array(buffer, start, length);
      }
      offset = start + length + ((4 - (length % 4)) % 4);
    }
    if (!json) { throw new Error("That .glb has no JSON chunk in it."); }
    return { json: json, bin: bin };
  }

  /** One accessor as a flat typed array, de-interleaved if it has to be. */
  function readAccessor(gltf, bin, index) {
    var accessor = (gltf.accessors || [])[index];
    if (!accessor) { return null; }
    var size = COMPONENTS[accessor.type] || 1;
    var Kind = ARRAYS[accessor.componentType];
    if (!Kind) { return null; }
    var count = accessor.count | 0;
    if (accessor.bufferView === undefined) {
      return { data: new Kind(count * size), size: size, count: count,
               componentType: accessor.componentType };
    }
    var viewSpec = (gltf.bufferViews || [])[accessor.bufferView];
    if (!viewSpec || !bin) { return null; }
    var base = (bin.byteOffset + (viewSpec.byteOffset || 0) +
                (accessor.byteOffset || 0));
    var stride = viewSpec.byteStride || 0;
    var packed = size * Kind.BYTES_PER_ELEMENT;
    var out;
    if (!stride || stride === packed) {
      // The common case out of Blender: tightly packed, so this is a view on
      // the same bytes rather than a copy of them.
      out = new Kind(bin.buffer, base, count * size);
    } else {
      out = new Kind(count * size);
      for (var i = 0; i < count; i++) {
        var row = new Kind(bin.buffer, base + i * stride, size);
        out.set(row, i * size);
      }
    }
    return { data: out, size: size, count: count,
             componentType: accessor.componentType };
  }

  /** The same accessor as floats, un-normalising integers on the way. */
  function readFloats(gltf, bin, index) {
    var read = readAccessor(gltf, bin, index);
    if (!read) { return null; }
    if (read.data instanceof Float32Array) { return read; }
    var divisor = NORMALISERS[read.componentType] || 1;
    var accessor = (gltf.accessors || [])[index] || {};
    var scale = accessor.normalized ? divisor : 1;
    var out = new Float32Array(read.data.length);
    for (var i = 0; i < read.data.length; i++) { out[i] = read.data[i] / scale; }
    return { data: out, size: read.size, count: read.count,
             componentType: 5126 };
  }

  // ---------------------------------------------------------- scene graph --

  function buildNodes(gltf) {
    var raw = gltf.nodes || [];
    var nodes = raw.map(function (node, index) {
      var entry = {
        index: index,
        name: node.name || ("node" + index),
        children: node.children || [],
        mesh: node.mesh,
        skin: node.skin,
        parent: -1,
        translation: (node.translation || [0, 0, 0]).slice(),
        rotation: (node.rotation || [0, 0, 0, 1]).slice(),
        scale: (node.scale || [1, 1, 1]).slice(),
        matrix: node.matrix ? new Float32Array(node.matrix) : null,
        local: new Float32Array(16),
        world: new Float32Array(16)
      };
      // Kept so the Play toggle can put a node back exactly where the bind
      // pose had it when playback stops.
      entry.restTranslation = entry.translation.slice();
      entry.restRotation = entry.rotation.slice();
      entry.restScale = entry.scale.slice();
      return entry;
    });
    nodes.forEach(function (node) {
      node.children.forEach(function (child) {
        if (nodes[child]) { nodes[child].parent = node.index; }
      });
    });
    // Parents before children, so one pass computes every world matrix.
    var order = [], seen = {};
    function visit(index) {
      if (seen[index] || !nodes[index]) { return; }
      seen[index] = true;
      order.push(index);
      nodes[index].children.forEach(visit);
    }
    nodes.forEach(function (node) { if (node.parent === -1) { visit(node.index); } });
    nodes.forEach(function (node) { visit(node.index); });   // cycles, defensively
    return { nodes: nodes, order: order };
  }

  function updateWorld(graph) {
    graph.order.forEach(function (index) {
      var node = graph.nodes[index];
      if (node.matrix) {
        node.local.set(node.matrix);
      } else {
        fromTRS(node.local, node.translation, node.rotation, node.scale);
      }
      if (node.parent === -1) {
        node.world.set(node.local);
      } else {
        multiply(node.world, graph.nodes[node.parent].world, node.local);
      }
    });
  }

  // ------------------------------------------------------------- the model --

  function loadModel(buffer) {
    var parsed = parseGLB(buffer);
    var gltf = parsed.json, bin = parsed.bin;
    var graph = buildNodes(gltf);
    updateWorld(graph);

    var primitives = [];
    var skinned = 0, vertices = 0, triangles = 0;
    graph.nodes.forEach(function (node) {
      if (node.mesh === undefined) { return; }
      var mesh = (gltf.meshes || [])[node.mesh];
      if (!mesh) { return; }
      (mesh.primitives || []).forEach(function (prim) {
        if (prim.mode !== undefined && prim.mode !== 4) { return; }  // TRIANGLES
        var attributes = prim.attributes || {};
        if (attributes.POSITION === undefined) { return; }
        var position = readFloats(gltf, bin, attributes.POSITION);
        if (!position) { return; }
        var normal = attributes.NORMAL === undefined
          ? null : readFloats(gltf, bin, attributes.NORMAL);
        var indices = prim.indices === undefined
          ? null : readAccessor(gltf, bin, prim.indices);
        // COLOR_0 is how a weight heatmap reaches a browser: glTF has no
        // vertex groups, so the bridge bakes one bone's influence into vertex
        // colours before it exports. VEC4 with alpha is common; only the rgb
        // is wanted here.
        var painted = null;
        if (attributes.COLOR_0 !== undefined) {
          var read = readFloats(gltf, bin, attributes.COLOR_0);
          if (read) {
            if (read.size === 3) {
              painted = read.data;
            } else if (read.size === 4) {
              painted = new Float32Array(read.count * 3);
              for (var v = 0; v < read.count; v++) {
                painted[v * 3] = read.data[v * 4];
                painted[v * 3 + 1] = read.data[v * 4 + 1];
                painted[v * 3 + 2] = read.data[v * 4 + 2];
              }
            }
          }
        }
        var entry = {
          node: node,
          name: node.name + "/" + (mesh.name || "mesh"),
          position: position.data,
          normal: normal ? normal.data : null,
          colour: painted,
          indices: indices ? indices.data : null,
          count: indices ? indices.count : position.count,
          vertices: position.count,
          skin: null
        };
        var skin = node.skin === undefined ? null : (gltf.skins || [])[node.skin];
        if (skin && attributes.JOINTS_0 !== undefined &&
            attributes.WEIGHTS_0 !== undefined) {
          var joints = readAccessor(gltf, bin, attributes.JOINTS_0);
          var weights = readFloats(gltf, bin, attributes.WEIGHTS_0);
          var inverse = skin.inverseBindMatrices === undefined
            ? null : readFloats(gltf, bin, skin.inverseBindMatrices);
          if (joints && weights) {
            entry.skin = {
              joints: joints.data,
              weights: weights.data,
              nodes: skin.joints || [],
              inverse: inverse ? inverse.data : null,
              // The buffer the skinned positions are rewritten into every
              // frame, allocated once: a per-frame allocation of a 15k-vertex
              // mesh is a garbage collector pause you can see.
              skinnedPosition: new Float32Array(position.data.length),
              skinnedNormal: normal
                ? new Float32Array(normal.data.length) : null,
              matrices: null
            };
            skinned += 1;
          }
        }
        vertices += entry.vertices;
        triangles += Math.floor(entry.count / 3);
        primitives.push(entry);
      });
    });

    var animations = (gltf.animations || []).map(function (animation, index) {
      var channels = [];
      var duration = 0;
      (animation.channels || []).forEach(function (channel) {
        var sampler = (animation.samplers || [])[channel.sampler];
        var target = channel.target || {};
        if (!sampler || target.node === undefined) { return; }
        if (["translation", "rotation", "scale"].indexOf(target.path) === -1) {
          return;   // morph weights are not something this viewer can show
        }
        var input = readFloats(gltf, bin, sampler.input);
        var output = readFloats(gltf, bin, sampler.output);
        if (!input || !output) { return; }
        duration = Math.max(duration, input.data[input.data.length - 1] || 0);
        channels.push({
          node: target.node, path: target.path,
          times: input.data, values: output.data,
          size: target.path === "rotation" ? 4 : 3,
          step: sampler.interpolation === "STEP"
        });
      });
      return { name: animation.name || ("animation " + (index + 1)),
               channels: channels, duration: duration };
    }).filter(function (animation) { return animation.channels.length > 0; });

    return {
      gltf: gltf, graph: graph, primitives: primitives,
      animations: animations, skinned: skinned,
      vertices: vertices, triangles: triangles,
      painted: primitives.some(function (one) { return !!one.colour; })
    };
  }

  // ------------------------------------------------------------- skinning --
  //
  // Linear blend skinning, on the CPU.  A uniform array of joint matrices is
  // the usual way and it is faster, but WebGL1's vertex-uniform budget is not
  // big enough for a Rigify deform skeleton on every machine, and a viewer
  // that silently showed a T-pose on the one character it was built for would
  // be worse than no playback at all.  15k vertices at four influences is a
  // few milliseconds a frame, which is what a preview can afford.

  function jointMatrices(model, primitive) {
    var skin = primitive.skin;
    var count = skin.nodes.length;
    if (!skin.matrices || skin.matrices.length !== count * 16) {
      skin.matrices = new Float32Array(count * 16);
    }
    var temp = new Float32Array(16), bind = new Float32Array(16);
    for (var j = 0; j < count; j++) {
      var node = model.graph.nodes[skin.nodes[j]];
      if (!node) { identity(temp); } else { temp.set(node.world); }
      if (skin.inverse) {
        bind.set(skin.inverse.subarray(j * 16, j * 16 + 16));
        multiply(skin.matrices.subarray(j * 16, j * 16 + 16), temp, bind);
      } else {
        skin.matrices.set(temp, j * 16);
      }
    }
    return skin.matrices;
  }

  function applySkin(model, primitive) {
    var skin = primitive.skin;
    var matrices = jointMatrices(model, primitive);
    var source = primitive.position, target = skin.skinnedPosition;
    var normals = primitive.normal, outNormals = skin.skinnedNormal;
    var joints = skin.joints, weights = skin.weights;
    var blended = new Float32Array(16);
    for (var v = 0; v < primitive.vertices; v++) {
      for (var k = 0; k < 16; k++) { blended[k] = 0; }
      var any = 0;
      for (var i = 0; i < 4; i++) {
        var weight = weights[v * 4 + i];
        if (!weight) { continue; }
        any += weight;
        var base = joints[v * 4 + i] * 16;
        for (var m = 0; m < 16; m++) { blended[m] += matrices[base + m] * weight; }
      }
      var x = source[v * 3], y = source[v * 3 + 1], z = source[v * 3 + 2];
      if (!any) {
        target[v * 3] = x; target[v * 3 + 1] = y; target[v * 3 + 2] = z;
        if (outNormals && normals) {
          outNormals[v * 3] = normals[v * 3];
          outNormals[v * 3 + 1] = normals[v * 3 + 1];
          outNormals[v * 3 + 2] = normals[v * 3 + 2];
        }
        continue;
      }
      target[v * 3] = blended[0] * x + blended[4] * y + blended[8] * z + blended[12];
      target[v * 3 + 1] = blended[1] * x + blended[5] * y + blended[9] * z + blended[13];
      target[v * 3 + 2] = blended[2] * x + blended[6] * y + blended[10] * z + blended[14];
      if (outNormals && normals) {
        var nx = normals[v * 3], ny = normals[v * 3 + 1], nz = normals[v * 3 + 2];
        // The blended matrix rather than its inverse transpose: a skinning
        // matrix is very nearly rigid, and a preview does not pay for the
        // difference.
        outNormals[v * 3] = blended[0] * nx + blended[4] * ny + blended[8] * nz;
        outNormals[v * 3 + 1] = blended[1] * nx + blended[5] * ny + blended[9] * nz;
        outNormals[v * 3 + 2] = blended[2] * nx + blended[6] * ny + blended[10] * nz;
      }
    }
    return target;
  }

  // ------------------------------------------------------------ animation --

  function sampleChannel(channel, time) {
    var times = channel.times, size = channel.size;
    var last = times.length - 1;
    if (last < 0) { return null; }
    if (time <= times[0]) { return channel.values.subarray(0, size); }
    if (time >= times[last]) {
      return channel.values.subarray(last * size, last * size + size);
    }
    var low = 0, high = last;
    while (high - low > 1) {
      var middle = (low + high) >> 1;
      if (times[middle] <= time) { low = middle; } else { high = middle; }
    }
    if (channel.step) {
      return channel.values.subarray(low * size, low * size + size);
    }
    var span = times[high] - times[low];
    var t = span > 0 ? (time - times[low]) / span : 0;
    var a = channel.values.subarray(low * size, low * size + size);
    var b = channel.values.subarray(high * size, high * size + size);
    var out = new Float32Array(size);
    if (size === 4) { return slerp(out, a, b, t); }
    for (var i = 0; i < size; i++) { out[i] = a[i] + (b[i] - a[i]) * t; }
    return out;
  }

  function poseAt(model, animation, time) {
    animation.channels.forEach(function (channel) {
      var node = model.graph.nodes[channel.node];
      if (!node) { return; }
      var value = sampleChannel(channel, time);
      if (!value) { return; }
      if (channel.path === "translation") {
        node.translation[0] = value[0];
        node.translation[1] = value[1];
        node.translation[2] = value[2];
      } else if (channel.path === "scale") {
        node.scale[0] = value[0]; node.scale[1] = value[1]; node.scale[2] = value[2];
      } else {
        node.rotation[0] = value[0]; node.rotation[1] = value[1];
        node.rotation[2] = value[2]; node.rotation[3] = value[3];
      }
      // A node the animation drives cannot also be using a baked matrix.
      node.matrix = null;
    });
    updateWorld(model.graph);
  }

  function restPose(model) {
    model.graph.nodes.forEach(function (node) {
      node.translation = node.restTranslation.slice();
      node.rotation = node.restRotation.slice();
      node.scale = node.restScale.slice();
    });
    updateWorld(model.graph);
  }

  // --------------------------------------------------------------- shaders --

  var VERTEX_SHADER = [
    "attribute vec3 position;",
    "attribute vec3 normal;",
    "attribute vec3 colour;",
    "uniform mat4 model;",
    "uniform mat4 viewProjection;",
    "varying vec3 vNormal;",
    "varying vec3 vPosition;",
    "varying vec3 vColour;",
    "void main() {",
    "  vec4 world = model * vec4(position, 1.0);",
    "  vPosition = world.xyz;",
    "  vNormal = mat3(model) * normal;",
    "  vColour = colour;",
    "  gl_Position = viewProjection * world;",
    "}"
  ].join("\n");

  // One clay material lit from the camera plus a cool fill, and an orange rim
  // so the silhouette reads against the page's own dark background — the same
  // accent the rest of this UI is built on.
  // `tint` and `alpha` are what let one shader draw three things: the clay
  // model, the same model ghosted back so the handles inside it read, and the
  // handles themselves.  `flat` drops the lighting for the drag line, which is
  // a measurement rather than a surface.
  var FRAGMENT_SHADER = [
    "precision mediump float;",
    "varying vec3 vNormal;",
    "varying vec3 vPosition;",
    "uniform vec3 eye;",
    "uniform vec3 tint;",
    "uniform float alpha;",
    "uniform float flat_;",
    "uniform float painted;",
    "varying vec3 vColour;",
    "void main() {",
    "  if (flat_ > 0.5) { gl_FragColor = vec4(tint, alpha); return; }",
    "  vec3 n = normalize(vNormal);",
    "  vec3 v = normalize(eye - vPosition);",
    "  if (dot(n, v) < 0.0) { n = -n; }",
    "  float key = max(dot(n, normalize(v + vec3(0.4, 0.7, 0.2))), 0.0);",
    "  float fill = max(dot(n, normalize(vec3(-0.6, -0.2, 0.5))), 0.0);",
    "  float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0);",
    // A weight heatmap is a MEASUREMENT painted on the mesh, so it keeps its
    // own colour and only takes enough shading to read as a surface. The clay
    // lighting would turn a blue thigh grey and lose the thing being shown.
    "  vec3 base = mix(tint, vColour, painted);",
    "  vec3 colour = base * (0.28 + 0.72 * key);",
    "  colour += vec3(0.10, 0.12, 0.16) * fill * (1.0 - painted);",
    "  colour += vec3(0.95, 0.55, 0.18) * rim * 0.55 * (1.0 - painted);",
    "  gl_FragColor = vec4(colour, alpha);",
    "}"
  ].join("\n");

  //: Pin colours, by the severity the gate reported.  Nothing invents a
  //: colour: an ``unmeasured`` gate is grey, not green.
  var PIN_COLOURS = {
    fail: [0.88, 0.30, 0.26],
    attention: [0.91, 0.69, 0.24],
    ok: [0.34, 0.78, 0.54],
    unknown: [0.45, 0.48, 0.55],
    none: [0.45, 0.48, 0.55]
  };
  //: A pin is bigger than a nudge handle — it is the thing being pointed at.
  var PIN_SCALE = 0.030;

  //: The clay the model is drawn in when nothing is being placed.
  var CLAY = [0.30, 0.31, 0.33];
  //: A handle nobody has taken hold of, the one that is selected, and the
  //: ghost left behind at the place a drag started.
  var HANDLE = [0.55, 0.60, 0.68];
  var HANDLE_ON = [0.95, 0.65, 0.22];
  var HANDLE_WAS = [0.40, 0.43, 0.50];
  //: How big a handle is, as a fraction of the model's own radius, and how
  //: near the pointer has to be (in CSS pixels) to take hold of one.
  var HANDLE_SCALE = 0.022;
  var HANDLE_PICK_PX = 18;
  //: How see-through the model goes while joints are being placed.  Enough
  //: that a handle behind a thigh is still findable, not so little that the
  //: silhouette stops being the reference the whole feature exists to give.
  var GHOST_ALPHA = 0.22;

  // ---------------------------------------------------------- the joints --
  //
  // The artist: "I don't know how much 10mm is here."  A handle at every
  // joint, moved against the model itself, is the answer to that sentence —
  // so these positions have to be the rig's real ones and not an estimate.
  //
  // They are.  A glTF skin lists its joints as NODES, and a bone's node sits
  // at that bone's HEAD, so every joint position here is read straight out of
  // the node hierarchy with its parents' transforms applied.  A bone's tail is
  // not a node of its own — for an interior bone it IS the head of its child,
  // which already has a handle, and for a leaf bone (a fingertip) glTF simply
  // does not carry it.  So this returns heads, every one exact, and says
  // nothing about the tips it cannot see rather than guessing at them.

  var DEFORM_PREFIX = "DEF-";

  //: Bone name -> what a person calls that joint.  Only the ones where the
  //: head of the bone has an unambiguous common name: the head of the foot is
  //: the ankle, the head of the shin is the knee.  Anything not in here keeps
  //: its own words, and the real bone name is shown beside the plain one
  //: either way — a friendly label that hid which bone is about to move would
  //: be worse than no label at all.
  var JOINT_WORDS = {
    foot: "ankle",
    shin: "knee",
    thigh: "hip",
    hand: "wrist",
    forearm: "elbow",
    upper_arm: "shoulder",
    shoulder: "collarbone",
    toe: "toe",
    neck: "neck",
    head: "head"
  };

  var SIDE_WORDS = { L: "left", R: "right", l: "left", r: "right" };

  /** ``"DEF-foot.L"`` -> ``"left ankle"``. Never invents a side or a joint. */
  function plainJointName(bone) {
    var text = String(bone || "").trim();
    if (!text) { return ""; }
    var stem = text.replace(/^(DEF|ORG|MCH)[-_]/, "");
    var side = "";
    var numbered = "";
    var match = /^(.*?)([._-])([LlRr])((?:\.\d+)*)$/.exec(stem);
    if (match) {
      stem = match[1];
      side = SIDE_WORDS[match[3]] || "";
      numbered = match[4];
    } else {
      var plain = /^(.*?)((?:\.\d+)+)$/.exec(stem);
      if (plain) { stem = plain[1]; numbered = plain[2]; }
    }
    var word = JOINT_WORDS[stem.toLowerCase()] || stem.replace(/_/g, " ");
    var out = side ? (side + " " + word) : word;
    if (numbered) {
      // ``.001`` is Blender's second bone of that name, not a decimal.
      var nth = parseInt(numbered.replace(/^\./, ""), 10);
      if (nth > 0) { out += " " + (nth + 1); }
    }
    return out;
  }

  /** Every joint in the model, as ``{bone, end, position, leaf}``.
   *
   * ``prefix`` filters to the deform chain, which is what a nudge may touch —
   * but a rig whose bones are not named that way still gets handles rather
   * than an empty viewport, because an empty viewport looks like a bug.
   */
  function jointHandles(model, prefix) {
    if (prefix === undefined) { prefix = DEFORM_PREFIX; }
    var gltf = (model && model.gltf) || {};
    var nodes = (model && model.graph && model.graph.nodes) || [];
    var seen = {};
    var joints = [];
    (gltf.skins || []).forEach(function (skin) {
      (skin.joints || []).forEach(function (index) {
        if (seen[index] || !nodes[index]) { return; }
        seen[index] = true;
        joints.push(nodes[index]);
      });
    });
    if (!joints.length) { return []; }

    var wanted = joints;
    var filtered = false;
    if (prefix) {
      var matching = joints.filter(function (node) {
        return String(node.name || "").indexOf(prefix) === 0;
      });
      if (matching.length) { wanted = matching; filtered = true; }
    }
    return wanted.map(function (node) {
      var hasJointChild = (node.children || []).some(function (child) {
        return seen[child];
      });
      return {
        bone: String(node.name || ""),
        label: plainJointName(node.name),
        end: "head",           // a joint node sits at its bone's head
        node: node.index,
        leaf: !hasJointChild,
        filtered: filtered,
        position: [node.world[12], node.world[13], node.world[14]]
      };
    });
  }

  //: glTF is Y-up and Blender is Z-up, and the exporter writes
  //: ``(x, y, z)_blender -> (x, z, -y)_gltf``.  Going back the other way is
  //: the one conversion between the handle the artist dragged and the bone
  //: the bridge moves, so it lives in one function with the mapping written
  //: down beside it.  Metres in, millimetres out.
  function toBlenderMillimetres(delta) {
    return [
      delta[0] * 1000,
      -delta[2] * 1000,
      delta[1] * 1000
    ];
  }

  /** The other way: a point Blender measured, in metres, put in the viewer. */
  function fromBlenderMetres(point) {
    return [point[0], point[2], -point[1]];
  }

  //: The three single axes a nudge may run along, in the viewer's own space.
  //: Single axes on purpose: "up a bit" is the common case and a one-axis drag
  //: cannot go sideways by accident, which free 3D dragging does constantly.
  var AXES = {
    up: { vector: [0, 1, 0], label: "up / down" },
    forward: { vector: [0, 0, 1], label: "forward / back" },
    side: { vector: [1, 0, 0], label: "side to side" }
  };

  /** A unit sphere as ``{position, normal, indices}`` — the handle's body. */
  function sphere(rings, segments) {
    var position = [], normal = [], indices = [];
    for (var y = 0; y <= rings; y++) {
      var phi = (y / rings) * Math.PI;
      for (var x = 0; x <= segments; x++) {
        var theta = (x / segments) * Math.PI * 2;
        var px = Math.sin(phi) * Math.cos(theta);
        var py = Math.cos(phi);
        var pz = Math.sin(phi) * Math.sin(theta);
        position.push(px, py, pz);
        normal.push(px, py, pz);
      }
    }
    for (var ring = 0; ring < rings; ring++) {
      for (var seg = 0; seg < segments; seg++) {
        var a = ring * (segments + 1) + seg;
        var b = a + segments + 1;
        indices.push(a, b, a + 1, a + 1, b, b + 1);
      }
    }
    return {
      position: new Float32Array(position),
      normal: new Float32Array(normal),
      indices: new Uint16Array(indices)
    };
  }

  function compile(gl, kind, source) {
    var shader = gl.createShader(kind);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      var log = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error("The viewer's shader would not compile: " + log);
    }
    return shader;
  }

  // ---------------------------------------------------------------- viewer --

  function create(canvas) {
    var gl = null;
    try {
      gl = canvas.getContext("webgl", { antialias: true, alpha: true }) ||
           canvas.getContext("experimental-webgl", { antialias: true });
    } catch (e) { gl = null; }
    if (!gl) {
      return { supported: false, error: "This browser has no WebGL, so the "
                                       + "model view cannot draw." };
    }

    var program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
    gl.bindAttribLocation(program, 0, "position");
    gl.bindAttribLocation(program, 1, "normal");
    gl.bindAttribLocation(program, 2, "colour");
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      return { supported: false,
               error: "The viewer's shader would not link: "
                      + gl.getProgramInfoLog(program) };
    }
    var uniforms = {
      model: gl.getUniformLocation(program, "model"),
      viewProjection: gl.getUniformLocation(program, "viewProjection"),
      eye: gl.getUniformLocation(program, "eye"),
      tint: gl.getUniformLocation(program, "tint"),
      alpha: gl.getUniformLocation(program, "alpha"),
      flat: gl.getUniformLocation(program, "flat_"),
      painted: gl.getUniformLocation(program, "painted")
    };

    var state = {
      model: null, buffers: [], frame: null,
      yaw: 0.7, pitch: 0.45, distance: 3, centre: [0, 0, 0], radius: 1,
      pan: [0, 0, 0], playing: false, animation: 0, time: 0, clock: 0,
      dirty: true, onFrame: null,
      // -- joint nudge mode --------------------------------------------
      nudging: false,
      handles: [],       // {bone, end, position, ...} straight off the glb
      picked: -1,        // which handle is selected, or -1
      origin: null,      // where the selected handle was before the drag
      axis: "up",        // which single axis a drag runs along
      height: 0,         // the model's own height, the to-scale reference
      onNudge: null,
      // -- stage inspection: the check's findings, on the model -----------
      pins: [],          // {id, position, severity, bone, label}
      pinned: -1,        // which pin is open, or -1
      onPin: null
    };
    var projection = new Float32Array(16);
    var view = new Float32Array(16);
    var viewProjection = new Float32Array(16);
    var eye = new Float32Array(3);
    var scratch = new Float32Array(16);

    // The handle's body and the line a drag leaves behind it, uploaded once.
    var ball = sphere(10, 14);
    var ballBuffers = null;
    var lineBuffer = null;
    var lineData = new Float32Array(6);

    function ensureHandleBuffers() {
      if (ballBuffers) { return ballBuffers; }
      var position = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, position);
      gl.bufferData(gl.ARRAY_BUFFER, ball.position, gl.STATIC_DRAW);
      var normal = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, normal);
      gl.bufferData(gl.ARRAY_BUFFER, ball.normal, gl.STATIC_DRAW);
      var indices = gl.createBuffer();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indices);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, ball.indices, gl.STATIC_DRAW);
      lineBuffer = gl.createBuffer();
      ballBuffers = { position: position, normal: normal, indices: indices,
                      count: ball.indices.length };
      return ballBuffers;
    }

    function material(tint, alpha, isFlat) {
      gl.uniform3f(uniforms.tint, tint[0], tint[1], tint[2]);
      gl.uniform1f(uniforms.alpha, alpha === undefined ? 1 : alpha);
      gl.uniform1f(uniforms.flat, isFlat ? 1 : 0);
    }

    function placeBall(out, at, size) {
      identity(out);
      out[0] = size; out[5] = size; out[10] = size;
      out[12] = at[0]; out[13] = at[1]; out[14] = at[2];
      return out;
    }

    /** Recompute the camera. Called by every draw, and by anything that has
     *  to turn a world point into a pixel.
     *
     *  Both, deliberately.  Picking used to read the matrix that only `draw`
     *  wrote, so a click that arrived before the first frame — a tab that was
     *  in the background while the snapshot loaded, which is the normal case
     *  on a second monitor — silently hit nothing at all.
     */
    function updateCamera() {
      var aspect = canvas.clientWidth / Math.max(1, canvas.clientHeight);
      perspective(projection, 0.9, aspect, state.radius * 0.01,
                  state.radius * 100);
      var cp = Math.cos(state.pitch), sp = Math.sin(state.pitch);
      eye[0] = state.centre[0] + state.pan[0] +
               state.distance * cp * Math.sin(state.yaw);
      eye[1] = state.centre[1] + state.pan[1] + state.distance * sp;
      eye[2] = state.centre[2] + state.pan[2] +
               state.distance * cp * Math.cos(state.yaw);
      lookAt(view, eye,
             [state.centre[0] + state.pan[0], state.centre[1] + state.pan[1],
              state.centre[2] + state.pan[2]], [0, 1, 0]);
      multiply(viewProjection, projection, view);
    }

    /** World point -> CSS pixels on this canvas, or ``null`` if behind us. */
    function project(point) {
      var vp = viewProjection;
      var x = vp[0] * point[0] + vp[4] * point[1] + vp[8] * point[2] + vp[12];
      var y = vp[1] * point[0] + vp[5] * point[1] + vp[9] * point[2] + vp[13];
      var w = vp[3] * point[0] + vp[7] * point[1] + vp[11] * point[2] + vp[15];
      if (!(w > 1e-6)) { return null; }
      return [(x / w * 0.5 + 0.5) * canvas.clientWidth,
              (1 - (y / w * 0.5 + 0.5)) * canvas.clientHeight];
    }

    function handleSize() {
      return Math.max(state.radius * HANDLE_SCALE, 1e-4);
    }

    /** The nearest of ``list`` to a point on screen, or -1. */
    function nearest(list, x, y) {
      updateCamera();
      var best = -1, bestDistance = HANDLE_PICK_PX * HANDLE_PICK_PX;
      list.forEach(function (item, index) {
        var at = project(item.position);
        if (!at) { return; }
        var dx = at[0] - x, dy = at[1] - y;
        var distance = dx * dx + dy * dy;
        if (distance <= bestDistance) { bestDistance = distance; best = index; }
      });
      return best;
    }

    /** The handle nearest to a point on screen, or -1. */
    function pick(x, y) {
      return nearest(state.handles, x, y);
    }

    /** The point on the MESH under a pixel, or ``null``.
     *
     * The weight brush needs a place on the surface, not a place in the air,
     * and a browser has no depth buffer to read back — so this is a real ray
     * cast against the triangles the viewer already holds.  Möller-Trumbore,
     * nearest hit wins.  Fifteen thousand triangles is a couple of
     * milliseconds on a click, which is what a click can afford.
     */
    function surfacePoint(x, y) {
      if (!state.model) { return null; }
      updateCamera();
      var width = canvas.clientWidth || 1, height = canvas.clientHeight || 1;
      var ndcX = (x / width) * 2 - 1;
      var ndcY = 1 - (y / height) * 2;
      // The camera's own axes, read out of the view matrix's rows.
      var right = [view[0], view[4], view[8]];
      var up = [view[1], view[5], view[9]];
      var forward = [-view[2], -view[6], -view[10]];
      var tanHalf = Math.tan(0.9 / 2);
      var aspect = width / height;
      var dir = [
        forward[0] + right[0] * ndcX * tanHalf * aspect + up[0] * ndcY * tanHalf,
        forward[1] + right[1] * ndcX * tanHalf * aspect + up[1] * ndcY * tanHalf,
        forward[2] + right[2] * ndcX * tanHalf * aspect + up[2] * ndcY * tanHalf
      ];
      var length = Math.hypot(dir[0], dir[1], dir[2]) || 1;
      dir[0] /= length; dir[1] /= length; dir[2] /= length;

      var best = Infinity, hit = null;
      state.model.primitives.forEach(function (primitive) {
        var points = primitive.position;
        if (primitive.skin) {
          // The skinned positions are written by the draw loop, and a click
          // can arrive before one has ever run — or while the tab is in the
          // background and the loop is paused. Skinning here costs a few
          // milliseconds and makes the answer independent of the frame rate.
          points = applySkin(state.model, primitive);
          if (!points) { return; }
        }
        var world = primitive.skin ? null : primitive.node.world;
        var indices = primitive.indices;
        var total = primitive.count;
        function vertex(slot) {
          var index = indices ? indices[slot] : slot;
          var px = points[index * 3], py = points[index * 3 + 1],
              pz = points[index * 3 + 2];
          if (!world) { return [px, py, pz]; }
          return [
            world[0] * px + world[4] * py + world[8] * pz + world[12],
            world[1] * px + world[5] * py + world[9] * pz + world[13],
            world[2] * px + world[6] * py + world[10] * pz + world[14]
          ];
        }
        for (var slot = 0; slot + 2 < total; slot += 3) {
          var a = vertex(slot), b = vertex(slot + 1), c = vertex(slot + 2);
          var e1 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
          var e2 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
          var p = [dir[1] * e2[2] - dir[2] * e2[1],
                   dir[2] * e2[0] - dir[0] * e2[2],
                   dir[0] * e2[1] - dir[1] * e2[0]];
          var det = e1[0] * p[0] + e1[1] * p[1] + e1[2] * p[2];
          if (Math.abs(det) < 1e-12) { continue; }
          var inv = 1 / det;
          var t = [eye[0] - a[0], eye[1] - a[1], eye[2] - a[2]];
          var u = (t[0] * p[0] + t[1] * p[1] + t[2] * p[2]) * inv;
          if (u < 0 || u > 1) { continue; }
          var q = [t[1] * e1[2] - t[2] * e1[1],
                   t[2] * e1[0] - t[0] * e1[2],
                   t[0] * e1[1] - t[1] * e1[0]];
          var v = (dir[0] * q[0] + dir[1] * q[1] + dir[2] * q[2]) * inv;
          if (v < 0 || u + v > 1) { continue; }
          var distance = (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2]) * inv;
          if (distance > 1e-5 && distance < best) {
            best = distance;
            hit = [eye[0] + dir[0] * distance, eye[1] + dir[1] * distance,
                   eye[2] + dir[2] * distance];
          }
        }
      });
      return hit;
    }

    /** How far one pixel of drag moves the handle, along the locked axis.
     *
     * Measured rather than derived from the field of view: the axis is
     * projected to the screen at the handle's own depth, and the pointer's
     * travel is the least-squares projection onto that screen direction.  So
     * an axis pointing almost at the camera moves slowly and an axis across
     * the screen moves one-to-one, which is what dragging should feel like.
     */
    function axisStep(handle, dx, dy) {
      var axis = (AXES[state.axis] || AXES.up).vector;
      var reach = Math.max(state.radius * 0.1, 1e-5);
      var from = project(handle.position);
      var to = project([handle.position[0] + axis[0] * reach,
                        handle.position[1] + axis[1] * reach,
                        handle.position[2] + axis[2] * reach]);
      if (!from || !to) { return null; }
      var sx = to[0] - from[0], sy = to[1] - from[1];
      var length = sx * sx + sy * sy;
      // Edge-on: the axis has no screen direction to drag along, and guessing
      // one would move the joint by an amount nobody could see.
      if (length < 4) { return null; }
      var along = ((dx * sx) + (dy * sy)) / length * reach;
      return [axis[0] * along, axis[1] * along, axis[2] * along];
    }

    function nudgeReadout() {
      var handle = state.handles[state.picked];
      if (!handle) { return { selected: false }; }
      var from = state.origin || handle.position;
      var delta = [handle.position[0] - from[0],
                   handle.position[1] - from[1],
                   handle.position[2] - from[2]];
      var metres = Math.hypot(delta[0], delta[1], delta[2]);
      return {
        selected: true,
        bone: handle.bone,
        label: handle.label,
        end: handle.end,
        axis: state.axis,
        axisLabel: (AXES[state.axis] || AXES.up).label,
        mm: metres * 1000,
        // The reference that answers "I don't know how much 10mm is here":
        // the move as a share of the model's own height, which is the thing
        // the artist is looking at.
        height_mm: state.height * 1000,
        share: state.height > 0 ? (metres / state.height) : 0,
        moved: metres > 1e-9,
        delta_mm: toBlenderMillimetres(delta)
      };
    }

    function tellNudge() {
      if (state.onNudge) { state.onNudge(nudgeReadout()); }
    }

    function releaseBuffers() {
      state.buffers.forEach(function (entry) {
        if (entry.position) { gl.deleteBuffer(entry.position); }
        if (entry.normal) { gl.deleteBuffer(entry.normal); }
        if (entry.colour) { gl.deleteBuffer(entry.colour); }
        if (entry.indices) { gl.deleteBuffer(entry.indices); }
      });
      state.buffers = [];
    }

    function upload(model) {
      releaseBuffers();
      model.primitives.forEach(function (primitive) {
        var entry = { primitive: primitive };
        entry.position = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, entry.position);
        gl.bufferData(gl.ARRAY_BUFFER, primitive.position,
                      primitive.skin ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
        if (primitive.normal) {
          entry.normal = gl.createBuffer();
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.normal);
          gl.bufferData(gl.ARRAY_BUFFER, primitive.normal,
                        primitive.skin ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
        }
        if (primitive.colour) {
          entry.colour = gl.createBuffer();
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.colour);
          gl.bufferData(gl.ARRAY_BUFFER, primitive.colour, gl.STATIC_DRAW);
        }
        if (primitive.indices) {
          entry.indices = gl.createBuffer();
          entry.indexType = primitive.indices instanceof Uint32Array
            ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
          if (primitive.indices instanceof Uint32Array &&
              !gl.getExtension("OES_element_index_uint")) {
            // No 32-bit indices on this machine: narrow them, which is safe
            // for anything a preview is ever asked to draw.
            var narrowed = new Uint16Array(primitive.indices.length);
            narrowed.set(primitive.indices);
            entry.indexType = gl.UNSIGNED_SHORT;
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, entry.indices);
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, narrowed, gl.STATIC_DRAW);
          } else {
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, entry.indices);
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, primitive.indices,
                          gl.STATIC_DRAW);
          }
        }
        state.buffers.push(entry);
      });
    }

    /** The bounding sphere of the whole model, in world space. */
    function measure(model) {
      var min = [Infinity, Infinity, Infinity];
      var max = [-Infinity, -Infinity, -Infinity];
      model.primitives.forEach(function (primitive) {
        var world = primitive.skin ? null : primitive.node.world;
        var data = primitive.position;
        for (var v = 0; v < primitive.vertices; v++) {
          var x = data[v * 3], y = data[v * 3 + 1], z = data[v * 3 + 2];
          if (world) {
            var wx = world[0] * x + world[4] * y + world[8] * z + world[12];
            var wy = world[1] * x + world[5] * y + world[9] * z + world[13];
            var wz = world[2] * x + world[6] * y + world[10] * z + world[14];
            x = wx; y = wy; z = wz;
          }
          if (x < min[0]) { min[0] = x; } if (x > max[0]) { max[0] = x; }
          if (y < min[1]) { min[1] = y; } if (y > max[1]) { max[1] = y; }
          if (z < min[2]) { min[2] = z; } if (z > max[2]) { max[2] = z; }
        }
      });
      if (!isFinite(min[0])) { return { centre: [0, 0, 0], radius: 1, height: 0 }; }
      var centre = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2,
                    (min[2] + max[2]) / 2];
      var radius = Math.max(
        Math.hypot(max[0] - centre[0], max[1] - centre[1], max[2] - centre[2]),
        1e-3);
      // The model's own height, which is the ruler a nudge is reported
      // against: "4 mm, a 250th of his height" says more than "4 mm".
      return { centre: centre, radius: radius, height: max[1] - min[1] };
    }

    function resize() {
      var ratio = Math.min(global.devicePixelRatio || 1, 2);
      var width = Math.max(1, Math.round(canvas.clientWidth * ratio));
      var height = Math.max(1, Math.round(canvas.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        state.dirty = true;
      }
    }

    function draw() {
      resize();
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.enable(gl.DEPTH_TEST);
      gl.enable(gl.CULL_FACE);
      gl.cullFace(gl.BACK);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (!state.model) { return; }

      updateCamera();

      gl.useProgram(program);
      gl.uniformMatrix4fv(uniforms.viewProjection, false, viewProjection);
      gl.uniform3f(uniforms.eye, eye[0], eye[1], eye[2]);
      gl.enableVertexAttribArray(0);
      gl.enableVertexAttribArray(1);

      // While joints are being placed the model goes translucent and stops
      // writing depth, so a handle inside a thigh is still visible and still
      // clickable — but the silhouette stays, because the silhouette IS the
      // ruler this whole feature exists to provide.  Pins want the same thing
      // for the same reason.
      if (state.nudging || state.pins.length) {
        material(CLAY, GHOST_ALPHA, false);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.depthMask(false);
        gl.disable(gl.CULL_FACE);
      } else {
        material(CLAY, 1, false);
        gl.disable(gl.BLEND);
        gl.depthMask(true);
      }

      state.buffers.forEach(function (entry) {
        var primitive = entry.primitive;
        if (primitive.skin) {
          // A skinned mesh's vertices are already in skin space, so the node
          // transform is not applied to it — glTF says so explicitly, and it
          // is why a rigged character drawn with its node matrix lands in the
          // wrong place.
          gl.uniformMatrix4fv(uniforms.model, false, identity(new Float32Array(16)));
          var skinned = applySkin(state.model, primitive);
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.position);
          gl.bufferSubData(gl.ARRAY_BUFFER, 0, skinned);
          if (entry.normal && primitive.skin.skinnedNormal) {
            gl.bindBuffer(gl.ARRAY_BUFFER, entry.normal);
            gl.bufferSubData(gl.ARRAY_BUFFER, 0, primitive.skin.skinnedNormal);
          }
        } else {
          gl.uniformMatrix4fv(uniforms.model, false, primitive.node.world);
        }
        gl.bindBuffer(gl.ARRAY_BUFFER, entry.position);
        gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        if (entry.normal) {
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.normal);
          gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
        } else {
          gl.disableVertexAttribArray(1);
          gl.vertexAttrib3f(1, 0, 0, 1);
        }
        if (entry.colour) {
          gl.enableVertexAttribArray(2);
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.colour);
          gl.vertexAttribPointer(2, 3, gl.FLOAT, false, 0, 0);
          gl.uniform1f(uniforms.painted, 1);
        } else {
          gl.disableVertexAttribArray(2);
          gl.uniform1f(uniforms.painted, 0);
        }
        if (entry.indices) {
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, entry.indices);
          gl.drawElements(gl.TRIANGLES, primitive.count, entry.indexType, 0);
        } else {
          gl.drawArrays(gl.TRIANGLES, 0, primitive.count);
        }
        if (!entry.normal) { gl.enableVertexAttribArray(1); }
      });
      gl.disableVertexAttribArray(2);
      gl.uniform1f(uniforms.painted, 0);

      if (state.nudging || state.pins.length) { drawHandles(); }

      gl.depthMask(true);
      gl.enable(gl.CULL_FACE);
      gl.disable(gl.BLEND);
      state.dirty = false;
    }

    function drawHandles() {
      var buffers = ensureHandleBuffers();
      var size = handleSize();
      gl.depthMask(true);
      gl.disable(gl.BLEND);
      gl.enable(gl.CULL_FACE);

      gl.bindBuffer(gl.ARRAY_BUFFER, buffers.position);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffers.normal);
      gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, buffers.indices);

      // Where the selected handle started, left behind in grey.  This is the
      // to-scale reference: the distance between the two balls, seen against
      // the leg they are on, is what "10 mm" actually looks like here.
      if (state.origin && state.picked >= 0) {
        material(HANDLE_WAS, 1, false);
        gl.uniformMatrix4fv(uniforms.model, false,
                            placeBall(scratch, state.origin, size * 0.8));
        gl.drawElements(gl.TRIANGLES, buffers.count, gl.UNSIGNED_SHORT, 0);
      }

      if (state.nudging) {
        state.handles.forEach(function (handle, index) {
          var chosen = index === state.picked;
          material(chosen ? HANDLE_ON : HANDLE, 1, false);
          gl.uniformMatrix4fv(uniforms.model, false,
            placeBall(scratch, handle.position, chosen ? size * 1.45 : size));
          gl.drawElements(gl.TRIANGLES, buffers.count, gl.UNSIGNED_SHORT, 0);
        });
      }

      // The findings, on the thing they were measured from.  Drawn after the
      // handles and bigger than them, because in a stage that has both, the
      // pin is what the artist came to look at.
      var pinSize = Math.max(state.radius * PIN_SCALE, 1e-4);
      state.pins.forEach(function (pin, index) {
        var open = index === state.pinned;
        material(PIN_COLOURS[pin.severity] || PIN_COLOURS.unknown, 1, false);
        gl.uniformMatrix4fv(uniforms.model, false,
          placeBall(scratch, pin.position, open ? pinSize * 1.5 : pinSize));
        gl.drawElements(gl.TRIANGLES, buffers.count, gl.UNSIGNED_SHORT, 0);
      });

      // The move itself, drawn as a line between where it was and where it is.
      var handle = state.handles[state.picked];
      if (handle && state.origin) {
        lineData[0] = state.origin[0];
        lineData[1] = state.origin[1];
        lineData[2] = state.origin[2];
        lineData[3] = handle.position[0];
        lineData[4] = handle.position[1];
        lineData[5] = handle.position[2];
        gl.bindBuffer(gl.ARRAY_BUFFER, lineBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, lineData, gl.DYNAMIC_DRAW);
        gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        gl.disableVertexAttribArray(1);
        gl.vertexAttrib3f(1, 0, 0, 1);
        material(HANDLE_ON, 1, true);
        gl.uniformMatrix4fv(uniforms.model, false, identity(scratch));
        gl.disable(gl.DEPTH_TEST);
        gl.drawArrays(gl.LINES, 0, 2);
        gl.enable(gl.DEPTH_TEST);
        gl.enableVertexAttribArray(1);
      }
    }

    function tick(now) {
      state.frame = global.requestAnimationFrame(tick);
      if (state.playing && state.model && state.model.animations.length) {
        var animation = state.model.animations[state.animation];
        var elapsed = state.clock ? (now - state.clock) / 1000 : 0;
        state.clock = now;
        if (animation.duration > 0) {
          state.time = (state.time + elapsed) % animation.duration;
        }
        poseAt(state.model, animation, state.time);
        state.dirty = true;
        if (state.onFrame) { state.onFrame(state.time, animation); }
      }
      if (state.dirty || canvas.clientWidth !== canvas._lastWidth) {
        canvas._lastWidth = canvas.clientWidth;
        draw();
      }
    }

    // -- input: orbit with the left button, pan with shift, zoom on wheel --
    var dragging = null;
    canvas.addEventListener("pointerdown", function (event) {
      var frame = canvas.getBoundingClientRect();
      var px = event.clientX - frame.left, py = event.clientY - frame.top;

      // A pin is the thing the artist came to click, so it is tried first —
      // and clicking one never orbits, because a finding card opening under a
      // spinning model is nobody's idea of a click.
      if (state.pins.length && !event.shiftKey) {
        var found = nearest(state.pins, px, py);
        if (found >= 0) {
          state.pinned = found;
          state.dirty = true;
          if (state.onPin) { state.onPin(state.pins[found], found); }
          return;
        }
      }

      // In nudge mode a press ON a handle takes hold of it; a press anywhere
      // else still orbits, so looking round the model never stops working.
      if (state.nudging && !event.shiftKey) {
        var box = frame;
        var hit = pick(px, py);
        if (hit >= 0) {
          if (hit !== state.picked) {
            state.picked = hit;
            state.origin = state.handles[hit].position.slice();
          }
          dragging = { x: event.clientX, y: event.clientY, joint: true };
          state.dirty = true;
          tellNudge();
          try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* old */ }
          return;
        }
      }
      dragging = { x: event.clientX, y: event.clientY, pan: event.shiftKey };
      try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* older */ }
    });
    canvas.addEventListener("pointermove", function (event) {
      if (!dragging) { return; }
      var dx = event.clientX - dragging.x, dy = event.clientY - dragging.y;
      dragging.x = event.clientX; dragging.y = event.clientY;
      if (dragging.joint) {
        var handle = state.handles[state.picked];
        if (handle) {
          var step = axisStep(handle, dx, dy);
          if (step) {
            handle.position[0] += step[0];
            handle.position[1] += step[1];
            handle.position[2] += step[2];
            state.dirty = true;
            tellNudge();
          }
        }
        return;
      }
      if (dragging.pan) {
        var scale = state.distance / Math.max(1, canvas.clientHeight);
        state.pan[0] -= dx * scale * Math.cos(state.yaw);
        state.pan[2] += dx * scale * Math.sin(state.yaw);
        state.pan[1] += dy * scale;
      } else {
        state.yaw -= dx * 0.008;
        state.pitch = Math.max(-1.5, Math.min(1.5, state.pitch + dy * 0.008));
      }
      state.dirty = true;
    });
    function endDrag() { dragging = null; }
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", endDrag);
    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      var factor = Math.exp((event.deltaY > 0 ? 1 : -1) * 0.12);
      state.distance = Math.max(state.radius * 0.15,
                                Math.min(state.radius * 40,
                                         state.distance * factor));
      state.dirty = true;
    }, { passive: false });

    state.frame = global.requestAnimationFrame(tick);

    return {
      supported: true,

      /** Draw one .glb, given as an ArrayBuffer. Returns what is in it. */
      show: function (buffer) {
        var model = loadModel(buffer);
        state.model = model;
        state.playing = false;
        state.time = 0;
        state.animation = 0;
        restPose(model);
        upload(model);
        var bounds = measure(model);
        state.centre = bounds.centre;
        state.radius = bounds.radius;
        state.height = bounds.height;
        state.distance = bounds.radius * 2.8;
        state.pan = [0, 0, 0];
        state.yaw = 0.7;
        state.pitch = 0.35;
        // A fresh snapshot is the new truth, so any half-finished placement
        // against the old one is dropped rather than carried over.
        state.handles = jointHandles(model);
        state.picked = -1;
        state.origin = null;
        state.pins = [];
        state.pinned = -1;
        state.dirty = true;
        return {
          objects: model.primitives.length,
          vertices: model.vertices,
          triangles: model.triangles,
          skinned: model.skinned,
          painted: !!model.painted,
          joints: state.handles.length,
          height_mm: bounds.height * 1000,
          animations: model.animations.map(function (a) {
            return { name: a.name, duration: a.duration };
          })
        };
      },

      // -- joint nudge mode ---------------------------------------------

      /** Turn handles on or off. Returns how many joints there are to place. */
      nudge: function (on) {
        state.nudging = !!on;
        if (!state.nudging) {
          this.cancelNudge();
        } else if (state.model && !state.handles.length) {
          state.handles = jointHandles(state.model);
        }
        state.dirty = true;
        return state.handles.length;
      },

      nudging: function () { return state.nudging; },

      // -- stage inspection: findings, on the model ----------------------

      /** Put the check's findings on the model. Returns how many landed.
       *
       * A finding either carries a world position (a defect the mesh check
       * located) or names a bone (a gate the rig check measured), and a bone
       * is resolved against the joints this glb already carries.  One that is
       * neither is not placed and not faked — it lists in the panel instead.
       */
      showPins: function (findings) {
        if (state.model && !state.handles.length) {
          state.handles = jointHandles(state.model);
        }
        var joints = {};
        state.handles.forEach(function (handle) {
          joints[handle.bone] = handle.position;
        });
        state.pins = [];
        state.pinned = -1;
        (findings || []).forEach(function (finding) {
          var at = null;
          if (finding.world_pos && finding.world_pos.length === 3) {
            at = fromBlenderMetres(finding.world_pos);
          } else if (finding.bone && joints[finding.bone]) {
            at = joints[finding.bone].slice();
          }
          if (!at) { return; }
          state.pins.push({
            id: finding.id, bone: finding.bone || "",
            label: finding.label || finding.gate || "",
            gate: finding.gate || "",
            severity: finding.severity || "unknown",
            position: at, finding: finding
          });
        });
        state.dirty = true;
        return state.pins.length;
      },

      clearPins: function () {
        state.pins = [];
        state.pinned = -1;
        state.dirty = true;
      },

      pins: function () {
        updateCamera();
        return state.pins.map(function (pin, index) {
          return { id: pin.id, bone: pin.bone, label: pin.label,
                   severity: pin.severity, index: index,
                   position: pin.position.slice(),
                   screen: project(pin.position) };
        });
      },

      onPin: function (fn) { state.onPin = fn; },

      /** Where on the mesh a pixel is, in BLENDER metres, or ``null``.
       *
       * Blender's axes on the way out, because the only caller is the weight
       * brush and the brush is a bridge route — one conversion, at the edge.
       */
      surfaceAt: function (x, y) {
        var found = surfacePoint(x, y);
        if (!found) { return null; }
        return { viewer: found,
                 blender: [found[0], -found[2], found[1]] };
      },

      /** Select the joint a pin sits on, ready to nudge it. */
      pinToHandle: function (bone) {
        var wanted = -1;
        state.handles.forEach(function (handle, index) {
          if (handle.bone === bone) { wanted = index; }
        });
        if (wanted < 0) { return false; }
        state.picked = wanted;
        state.origin = state.handles[wanted].position.slice();
        state.dirty = true;
        tellNudge();
        return true;
      },

      // -- the clip list and the scrubber ---------------------------------

      actions: function () {
        if (!state.model) { return []; }
        return state.model.animations.map(function (animation, index) {
          return { name: animation.name, duration: animation.duration,
                   index: index, playing: state.playing &&
                                          state.animation === index };
        });
      },

      /** Put the clip at a fraction of its length and hold it there. */
      seek: function (fraction) {
        if (!state.model || !state.model.animations.length) { return null; }
        var animation = state.model.animations[state.animation];
        var where = Math.max(0, Math.min(1, Number(fraction) || 0));
        state.playing = false;
        state.clock = 0;
        state.time = animation.duration * where;
        poseAt(state.model, animation, state.time);
        state.dirty = true;
        return this.at();
      },

      /** Where the current clip is right now, in seconds and in frames. */
      at: function () {
        if (!state.model || !state.model.animations.length) { return null; }
        var animation = state.model.animations[state.animation];
        var span = animation.duration || 0;
        return {
          name: animation.name, index: state.animation,
          time: state.time, duration: span,
          fraction: span > 0 ? (state.time / span) : 0,
          // glTF carries seconds; 24 fps is what every Forge clip is authored
          // at (`render_animation` and `rigforge_walk` both assume it), so the
          // frame number is a reading rather than a guess.
          frame: Math.round(state.time * 24),
          frames: Math.round(span * 24),
          playing: state.playing
        };
      },

      /** Every handle with where it currently is on screen, or ``null``.
       *
       * The same projection ``pick`` uses, exposed so that "the handle is
       * drawn there but clicking it does nothing" is a question something can
       * answer rather than a thing to squint at.
       */
      handles: function () {
        updateCamera();
        return state.handles.map(function (handle, index) {
          return { bone: handle.bone, label: handle.label, index: index,
                   position: handle.position.slice(),
                   screen: project(handle.position) };
        });
      },

      /** Which single axis a drag runs along. */
      axis: function (which) {
        if (which && AXES[which]) { state.axis = which; state.dirty = true; }
        return state.axis;
      },

      axes: function () {
        return Object.keys(AXES).map(function (key) {
          return { key: key, label: AXES[key].label };
        });
      },

      onNudge: function (fn) { state.onNudge = fn; },

      /** What is selected and how far it has moved, right now. */
      readout: function () { return nudgeReadout(); },

      /** Put the selected handle back where it was and let go of it. */
      cancelNudge: function () {
        var handle = state.handles[state.picked];
        if (handle && state.origin) {
          handle.position[0] = state.origin[0];
          handle.position[1] = state.origin[1];
          handle.position[2] = state.origin[2];
        }
        state.picked = -1;
        state.origin = null;
        state.dirty = true;
        tellNudge();
      },

      /** The move to send, in Blender's axes and millimetres, or ``null``.
       *
       * Nothing is applied here: this viewer draws a snapshot, and the only
       * thing that can move a real bone is the bridge.  The page sends this,
       * then takes a fresh snapshot, so what is on screen afterwards is what
       * Blender actually did rather than what was asked for.
       */
      commitNudge: function () {
        var read = nudgeReadout();
        if (!read.selected || !read.moved) { return null; }
        return { bone: read.bone, end: read.end, delta_mm: read.delta_mm,
                 label: read.label, mm: read.mm };
      },

      play: function (index) {
        if (!state.model || !state.model.animations.length) { return false; }
        state.animation = Math.max(0, Math.min(
          state.model.animations.length - 1, index || 0));
        state.playing = true;
        state.clock = 0;
        return true;
      },

      pause: function () {
        state.playing = false;
        state.clock = 0;
      },

      stop: function () {
        state.playing = false;
        state.clock = 0;
        state.time = 0;
        if (state.model) { restPose(state.model); state.dirty = true; }
      },

      playing: function () { return state.playing; },

      recentre: function () {
        if (!state.model) { return; }
        state.yaw = 0.7;
        state.pitch = 0.35;
        state.pan = [0, 0, 0];
        state.distance = state.radius * 2.8;
        state.dirty = true;
      },

      clear: function () {
        state.playing = false;
        state.model = null;
        releaseBuffers();
        state.dirty = true;
      },

      onFrame: function (fn) { state.onFrame = fn; },

      dispose: function () {
        if (state.frame) { global.cancelAnimationFrame(state.frame); }
        releaseBuffers();
        state.model = null;
      }
    };
  }

  global.ForgeGLB = {
    create: create,
    // Exported so they can be exercised without a canvas — the parser and the
    // joint maths are the halves of this file that can be wrong in a way
    // nobody sees until a bone lands in the wrong place.
    parseGLB: parseGLB,
    loadModel: loadModel,
    readAccessor: readAccessor,
    sampleChannel: sampleChannel,
    jointHandles: jointHandles,
    plainJointName: plainJointName,
    toBlenderMillimetres: toBlenderMillimetres,
    fromBlenderMetres: fromBlenderMetres
  };
}(window));
