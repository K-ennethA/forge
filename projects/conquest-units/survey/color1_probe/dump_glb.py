import json, struct, sys
p = sys.argv[-1]
d = open(p, 'rb').read()
jl, jt = struct.unpack_from('<II', d, 12)
j = json.loads(d[20:20+jl])
bin_off = 20 + jl + 8
for m in j['meshes']:
    for pi, prim in enumerate(m['primitives']):
        print("mesh", m['name'], "prim", pi, "attributes:", prim['attributes'])
        for k, ai in prim['attributes'].items():
            a = j['accessors'][ai]
            print("  accessor", ai, k, a['componentType'], a['type'], "count", a['count'], "normalized", a.get('normalized'))
            if k.startswith('COLOR'):
                bv = j['bufferViews'][a['bufferView']]
                off = bin_off + bv.get('byteOffset', 0) + a.get('byteOffset', 0)
                ncomp = {'VEC3': 3, 'VEC4': 4}[a['type']]
                fmt = {5126: 'f', 5123: 'H', 5121: 'B'}[a['componentType']]
                sz = struct.calcsize(fmt)
                stride = bv.get('byteStride', ncomp*sz)
                vals = []
                for i in range(a['count']):
                    v = struct.unpack_from('<'+fmt*ncomp, d, off+i*stride)
                    if fmt == 'H': v = tuple(x/65535 for x in v)
                    if fmt == 'B': v = tuple(x/255 for x in v)
                    vals.append(tuple(round(x, 3) for x in v))
                print("   values:", sorted(set(vals)))
