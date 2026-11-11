# -*- coding: utf-8 -*-
"""Render one PNG from an ODB (contour plot) or an INP (assembly with BC / load symbols).
Run by abq.py:  abaqus cae noGUI=cae_render.py -- <params.json>      (Abaqus Python 2.7)
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import sys


def quiet(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:
        return None


def main():
    P = json.load(open(sys.argv[-1]))
    vp = session.viewports['Viewport: 1']
    quiet(vp.makeCurrent)
    quiet(vp.maximize)
    quiet(session.graphicsOptions.setValues, backgroundStyle=SOLID, backgroundColor='#FFFFFF')
    quiet(session.printOptions.setValues, vpDecorations=OFF, vpBackground=ON, reduceColors=False)
    quiet(session.pngOptions.setValues, imageSize=(int(P['width']), int(P['height'])))
    info = ''
    if P['kind'] == 'odb':
        odb = session.openOdb(name=str(P['path']), readOnly=True)
        vp.setValues(displayedObject=odb)
        od = vp.odbDisplay
        names = odb.steps.keys()
        si = len(names) - 1
        if P.get('step') is not None:
            s = str(P['step'])
            if s in names:
                si = names.index(s)
            elif s.lstrip('-').isdigit():
                si = int(s) % len(names)
        frames = odb.steps[names[si]].frames
        fi = len(frames) - 1 if P.get('frame') is None else int(P['frame'])
        od.setFrame(step=si, frame=fi)
        fo = frames[fi].fieldOutputs
        var = str(P.get('var') or ('S' if 'S' in fo.keys() else 'U'))
        if var not in fo.keys():
            raise ValueError('field %s not in this frame; available: %s' % (var, ', '.join(fo.keys())))
        f = fo[var]
        pos = f.locations[0].position
        ref = None
        if P.get('comp'):
            ref = (COMPONENT, str(P['comp']))
        elif P.get('inv'):
            ref = (INVARIANT, str(P['inv']))
        elif MISES in f.validInvariants:
            ref = (INVARIANT, 'Mises')
        elif MAGNITUDE in f.validInvariants:
            ref = (INVARIANT, 'Magnitude')
        if ref:
            od.setPrimaryVariable(variableLabel=var, outputPosition=pos, refinement=ref)
        else:
            od.setPrimaryVariable(variableLabel=var, outputPosition=pos)
        deformed = P.get('deformed') and 'U' in fo.keys()
        od.display.setValues(plotState=(CONTOURS_ON_DEF if deformed else CONTOURS_ON_UNDEF,))
        if P.get('scale'):
            quiet(od.commonOptions.setValues, deformationScaling=UNIFORM, uniformScaleFactor=float(P['scale']))
        quiet(vp.viewportAnnotationOptions.setValues, legendFont='-*-verdana-medium-r-normal-*-*-120-*-*-p-*-*-*',
              titleFont='-*-verdana-medium-r-normal-*-*-100-*-*-p-*-*-*', stateFont='-*-verdana-medium-r-normal-*-*-100-*-*-p-*-*-*')
        info = 'step=%s frame=%d var=%s %s %s' % (names[si], fi, var, ref[1] if ref else '',
                                                 'deformed' if deformed else 'undeformed')
    else:
        m = mdb.ModelFromInputFile(name='PREVIEW', inputFileName=str(P['path']))
        a = m.rootAssembly
        vp.setValues(displayedObject=a)
        names = m.steps.keys()
        sname = str(P['step']) if P.get('step') and str(P['step']) in names else names[-1]
        quiet(vp.assemblyDisplay.setValues, step=sname)
        quiet(vp.assemblyDisplay.setValues, loads=ON, bcs=ON, predefinedFields=ON, interactions=ON, constraints=ON,
              connectors=ON)
        quiet(vp.assemblyDisplay.setValues, mesh=ON)
        quiet(vp.assemblyDisplay.meshOptions.setValues, meshTechnique=OFF)
        info = 'model from INP, step=%s, symbols: BC/loads/interactions' % sname
    vp.view.setValues(session.views[str(P['view'])])
    quiet(vp.view.fitView)
    session.printToFile(fileName=str(P['out']), format=PNG, canvasObjects=(vp,))
    print('RENDER_INFO ' + info)


main()
