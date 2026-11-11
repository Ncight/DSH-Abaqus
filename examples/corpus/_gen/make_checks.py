# -*- coding: utf-8 -*-
"""Writes the small hand-made models under examples/corpus/checks/ that each trigger one 体检 check.

    py make_checks.py

Plain Python, no Abaqus needed.  All models use a 2 x 2 x 2 block of C3D8R elements (27 nodes) so that
they stay readable; units are m - t - s (kN, kPa, t/m3) unless a model is about wrong units.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'checks')


def block(origin=(0.0, 0.0, 0.0), size=(1.0, 1.0, 1.0), n=(2, 2, 2), node0=1, elem0=1):
    """structured hex block -> (node lines, element lines, {name: [node labels]}, {name: [element labels]})"""
    nx, ny, nz = n
    nodes, elems = [], []
    nid = {}
    lab = node0
    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                nid[(i, j, k)] = lab
                nodes.append('%6d, %10.5f, %10.5f, %10.5f' % (
                    lab, origin[0] + size[0] * i / nx, origin[1] + size[1] * j / ny, origin[2] + size[2] * k / nz))
                lab += 1
    el = elem0
    eid = {}
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                c = [nid[(i, j, k)], nid[(i + 1, j, k)], nid[(i + 1, j + 1, k)], nid[(i, j + 1, k)],
                     nid[(i, j, k + 1)], nid[(i + 1, j, k + 1)], nid[(i + 1, j + 1, k + 1)], nid[(i, j + 1, k + 1)]]
                eid[(i, j, k)] = el
                elems.append('%5d, ' % el + ', '.join('%4d' % v for v in c))
                el += 1
    nsets = {
        'BOTTOM': [v for (i, j, k), v in nid.items() if j == 0],       # y = 0 face
        'TOP': [v for (i, j, k), v in nid.items() if j == ny],
        'XMIN': [v for (i, j, k), v in nid.items() if i == 0],
        'ZMIN': [v for (i, j, k), v in nid.items() if k == 0],
        'ALL': sorted(nid.values()),
    }
    elsets = {
        'ALL': sorted(eid.values()),
        'ELBOTTOM': [v for (i, j, k), v in eid.items() if j == 0],     # face S3 of these is the y = 0 face
        'ELTOP': [v for (i, j, k), v in eid.items() if j == ny - 1],   # face S5 of these is the y = max face
        'HALF1': [v for (i, j, k), v in eid.items() if i == 0],
        'HALF2': [v for (i, j, k), v in eid.items() if i > 0],
    }
    return nodes, elems, nsets, elsets


def lines16(vals):
    vals = sorted(vals)
    return [', '.join(str(v) for v in vals[i:i + 12]) for i in range(0, len(vals), 12)]


def setblock(kw, name, vals, extra=''):
    return ['*%s, %s=%s%s' % (kw, kw.lower(), name, extra)] + lines16(vals)


STEEL = ['*Material, name=Steel', '*Elastic', ' 2.06e8, 0.3', '*Density', ' 7.85,']


def write(name, lines, note):
    p = os.path.join(OUT, name)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write('*Heading\n ' + note + '\n** written by examples/corpus/_gen/make_checks.py\n')
        f.write('\n'.join(lines) + '\n')
    print('wrote', p)


def flat(nodes, elems, nsets, elsets, sections, materials, bcs, step, extra_model=()):
    out = ['*Node'] + nodes + ['*Element, type=C3D8R, elset=ALL'] + elems
    for k, v in nsets.items():
        out += setblock('Nset', k, v)
    for k, v in elsets.items():
        if k != 'ALL':
            out += setblock('Elset', k, v)
    out += list(sections) + list(extra_model) + list(materials) + ['*Boundary'] + list(bcs) + list(step)
    return out


def static_step(loads, name='Load'):
    return ['*Step, name=%s, nlgeom=NO' % name, '*Static', '1., 1., 1e-05, 1.'] + list(loads) + [
        '*Output, field, variable=PRESELECT', '*End Step']


def unused_nodes():
    n, e, ns, es = block()
    n += ['  9001,    0.25000,    1.20000,    0.25000', '  9002,    0.50000,    1.20000,    0.50000',
          '  9003,    0.75000,    1.20000,    0.75000']
    write('chk_unused_nodes.inp',
          flat(n, e, ns, es, ['*Solid Section, elset=ALL, material=Steel', ','], STEEL,
               ['BOTTOM, ENCASTRE'], static_step(['*Dsload', 'TOPSURF, P, 100.']),
               ['*Surface, type=ELEMENT, name=TOPSURF', 'ELTOP, S5']),
          'three nodes (9001-9003) that no element uses')


def contact_empty_surface():
    n1, e1, ns1, es1 = block()
    n2, e2, ns2, es2 = block(origin=(0.0, 1.0, 0.0), node0=101, elem0=101)
    out = ['*Node'] + n1 + n2 + ['*Element, type=C3D8R, elset=LOWER'] + e1 + ['*Element, type=C3D8R, elset=UPPER'] + e2
    out += setblock('Nset', 'BOTTOM', ns1['BOTTOM']) + setblock('Nset', 'UPTOP', ns2['TOP'])
    out += setblock('Elset', 'ELTOP', es1['ELTOP']) + setblock('Elset', 'UPTOPEL', es2['ELTOP'])
    out += ['*Elset, elset=NOTHING, generate', ' 9000, 9010, 1']          # no such elements: an empty set
    out += ['*Solid Section, elset=LOWER, material=Steel', ',', '*Solid Section, elset=UPPER, material=Steel', ',']
    out += ['*Surface, type=ELEMENT, name=LowerTop', 'ELTOP, S5',
            '*Surface, type=ELEMENT, name=UpperBottom', 'NOTHING, S3',
            '*Surface, type=ELEMENT, name=UpperTop', 'UPTOPEL, S5']
    out += ['*Surface Interaction, name=Fric', '1.,', '*Friction', ' 0.3,']
    out += ['*Contact Pair, interaction=Fric, type=SURFACE TO SURFACE', 'UpperBottom, LowerTop']
    out += STEEL + ['*Boundary', 'BOTTOM, ENCASTRE', 'UPTOP, 1, 1', 'UPTOP, 3, 3']
    out += static_step(['*Dsload', 'UpperTop, P, 100.'])
    write('chk_contact_empty_surface.inp', out, 'contact pair whose slave surface UpperBottom has no faces')


def material_models():
    n, e, ns, es = block()
    two = ['*Solid Section, elset=HALF1, material=%s', ',', '*Solid Section, elset=HALF2, material=%s', ',']
    surf = ['*Surface, type=ELEMENT, name=TOPSURF', 'ELTOP, S5']
    load = static_step(['*Dsload', 'TOPSURF, P, 100.'])

    def model(name, m1, m2, mats, note):
        write(name, flat(n, e, ns, es, [s % m if '%s' in s else s for s, m in zip(two, (m1, '', m2, ''))],
                         mats, ['BOTTOM, ENCASTRE'], load, surf), note)

    model('chk_material_poisson.inp', 'Rubber', 'Auxetic',
          ['*Material, name=Rubber', '*Elastic', ' 1.0e4, 0.5', '*Density', ' 1.2,',
           '*Material, name=Auxetic', '*Elastic', ' 5.0e5, -0.2', '*Density', ' 0.9,'],
          'Poisson ratio 0.5 (Rubber) and negative (Auxetic)')
    model('chk_material_negative.inp', 'ZeroE', 'NegDensity',
          ['*Material, name=ZeroE', '*Elastic', ' 0., 0.3', '*Density', ' 7.85,',
           '*Material, name=NegDensity', '*Elastic', ' 2.06e8, 0.3', '*Density', ' -7.85,'],
          'elastic modulus 0 (ZeroE) and negative density (NegDensity)')
    # the classic: modulus in Pa with density in t/m3, and modulus in kPa with density in kg/m3.
    # sqrt(E/rho) = 161994 and 162: both still look like "some material" to a generic magnitude check.
    model('chk_material_steel_density.inp', 'Steel-Q345', 'Q235',
          ['*Material, name=Steel-Q345', '*Elastic', ' 2.06e11, 0.3', '*Density', ' 7.85,',
           '*Material, name=Q235', '*Elastic', ' 2.06e8, 0.3', '*Density', ' 7850.,'],
          'steel with density / modulus units off by 1000 (Pa with t/m3, kPa with kg/m3)')
    model('chk_material_concrete_units.inp', 'Concrete-C30', 'C40',
          ['*Material, name=Concrete-C30', '*Elastic', ' 3.0e4, 0.2', '*Density', ' 2500.,',
           '*Material, name=C40', '*Elastic', ' 3.25e10, 0.2', '*Density', ' 2500.,'],
          'concrete C30 with E in MPa but density in kg/m3; C40 next to it is consistent (Pa, kg/m3)')
    # nearly incompressible on ordinary elements: Abaqus/Standard wants hybrid elements above 0.495
    model('chk_material_near_incompressible.inp', 'SoftClay', 'Steel',
          ['*Material, name=SoftClay', '*Elastic', ' 3.0e4, 0.499', '*Density', ' 1.8,',
           '*Material, name=Steel', '*Elastic', ' 2.06e8, 0.3', '*Density', ' 7.85,'],
          'Poisson ratio 0.499 (SoftClay) on ordinary, non-hybrid C3D8R elements')
    # Chinese material names: the name-based checks still recognise them, but Abaqus 2021 rejects the
    # input ("Invalid value ... for parameter name") - the preview says so
    model('chk_name_non_ascii.inp', '钢材', '混凝土C30',
          ['*Material, name=钢材', '*Elastic', ' 2.06e8, 0.3', '*Density', ' 7850.,',
           '*Material, name=混凝土C30', '*Elastic', ' 3.0e4, 0.2', '*Density', ' 2500.,'],
          'material names in Chinese (rejected by Abaqus) with inconsistent units')


def rigid_body_motion():
    n, e, ns, es = block()
    surf = ['*Surface, type=ELEMENT, name=TOPSURF', 'ELTOP, S5']
    # rollers under the block and a guided side: nothing holds it in U3
    write('chk_rigid_body_motion.inp',
          flat(n, e, ns, es, ['*Solid Section, elset=ALL, material=Steel', ','], STEEL,
               ['BOTTOM, 2, 2', 'XMIN, 1, 1'], static_step(['*Dsload', 'TOPSURF, P, 100.']), surf),
          'static step with load, no restraint at all in U3')


PART = None


def part_based(name, note, upper_shift, bcs, loads, interaction=(), assembly_extra=()):
    """two instances of one block part: Lower-1 at the origin, Upper-1 shifted"""
    n, e, ns, es = block()
    out = ['*Part, name=Block', '*Node'] + n + ['*Element, type=C3D8R'] + e
    out += ['*Nset, nset=ALL, generate', ' 1, 27, 1', '*Elset, elset=ALL, generate', ' 1, 8, 1']
    out += setblock('Nset', 'BOTTOM', ns['BOTTOM']) + setblock('Nset', 'TOP', ns['TOP'])
    out += setblock('Elset', 'ELTOP', es['ELTOP']) + setblock('Elset', 'ELBOTTOM', es['ELBOTTOM'])
    out += ['*Surface, type=ELEMENT, name=TopSurf', 'ELTOP, S5', '*Surface, type=ELEMENT, name=BottomSurf',
            'ELBOTTOM, S3', '*Solid Section, elset=ALL, material=Steel', ',', '*End Part']
    out += ['*Assembly, name=Assembly', '*Instance, name=Lower-1, part=Block', '*End Instance',
            '*Instance, name=Upper-1, part=Block', '%s, %s, %s' % upper_shift, '*End Instance']
    out += list(assembly_extra) + ['*End Assembly'] + STEEL + list(interaction)
    out += ['*Boundary'] + list(bcs) + static_step(loads)
    write(name, out, note)


def instance_models():
    fric = ['*Surface Interaction, name=Fric', '1.,', '*Friction', ' 0.3,']
    # Upper-1 rests on Lower-1 and is only held by the contact pair
    part_based('chk_rigid_body_motion_contact.inp',
               'Upper-1 carries load but has no boundary condition: only the contact pair holds it',
               (0.0, 1.0, 0.0), ['Lower-1.BOTTOM, ENCASTRE'], ['*Dsload', 'Upper-1.TopSurf, P, 100.'],
               fric + ['*Contact Pair, interaction=Fric, type=SURFACE TO SURFACE',
                       'Upper-1.BottomSurf, Lower-1.TopSurf'])
    # Upper-1 floats 2 m away: no boundary condition, no tie, no contact
    part_based('chk_unrestrained_instance.inp',
               'Upper-1 carries load but is not restrained and not connected to anything',
               (3.0, 0.0, 0.0), ['Lower-1.BOTTOM, ENCASTRE'],
               ['*Dsload', 'Upper-1.TopSurf, P, 100.', 'Lower-1.TopSurf, P, 100.'])


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    unused_nodes()
    contact_empty_surface()
    material_models()
    rigid_body_motion()
    instance_models()
