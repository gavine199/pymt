'''
Scatter package: provide lot of widgets based on scatter (base, svg, plane, image...)
'''

__all__ = ('MTScatterWidget', 'MTScatterSvg', 'MTScatterPlane',
           'MTScatterImage', 'MTScatter')

import pymt
from pymt.logger import pymt_logger
from pymt.utils import deprecated, serialize_numpy, deserialize_numpy, boundary

from pymt.ui.widgets.widget import MTWidget
from pymt.ui.widgets.svg import MTSvg
from pymt.ui.factory import MTWidgetFactory
from pymt.ui.animation import Animation, AnimationAlpha

from pymt.vector import Vector
from pymt.geometry import minimum_bounding_circle
from pymt.lib.transformations import *
from math import atan,cos, radians, degrees

from OpenGL.GL import *
from pymt.graphx import *


class MTScatter(MTWidget):
    '''MTScatter is a scatter widget based on MTWidget.
    You can scale, rotate and move with one and two finger.

    :Parameters:
        `rotation` : float, default to 0.0
            Set initial rotation of widget
        `translation` : list, default to (0,0)
            Set the initial translation of widget
        `scale` : float, default to 1.0
            Set the initial scaling of widget
        `do_rotation` : boolean, default to True
            Set to False for disabling rotation
        `do_translation` : boolean or list, default to True
            Set to False for disabling translation, and ['x'], ['y'] for limit translation only on x or y
        `do_scale` : boolean, default to True
            Set to False for disabling scale
        `auto_bring_to_front` : boolean, default to True
            Set to False for disabling widget bring to front
        `scale_min` : float, default to 0.01
            Minimum scale allowed. Don't set to 0, or you can have error with singular matrix.
            The 0.01 mean you can de-zoom up to 10000% (1/0.01*100).
        `scale_max` : float, default to None
            Maximum scale allowed.

    :Events:
        `on_transform` (rotation, scale, trans, intersect)
            Fired whenever the Scatter Widget is transformed (rotate, scale, moved, or zoomed).
    '''

    def __init__(self, **kwargs):
        super(MTScatter, self).__init__(**kwargs)

        self.register_event_type('on_transform')

        # private properties
        self._touches = []
        self._last_touch_pos = {} #we keep track ourselves so we dont have to recompute to local parent space

        self._transform        = identity_matrix()
        self._transform_inv    = identity_matrix()
        self._transform_gl     = identity_matrix().T.tolist()  #openGL matrix
        self._transform_inv_gl = identity_matrix().T.tolist()  #openGL matrix
        self.update_matrices()

        #enable/dissable features
        self.auto_bring_to_front = kwargs.get('auto_bring_to_front', True)
        self.do_translation = kwargs.get('do_translation', True)
        self.do_rotation    = kwargs.get('do_rotation', True)
        self.scale_min      = kwargs.get('scale_min', 0.01)
        self.scale_max      = kwargs.get('scale_max', 1e20)
        self.do_scale       = kwargs.get('do_scale', True)

        #inital transformation
        self.scale = kwargs.get('scale',1)
        self.rotation = kwargs.get('rotation', 0)
        if kwargs.get('pos') and kwargs.get('center'):
            pymt_logger.exception("both 'pos' and 'center' set in MTScatter constructor, only use one of the two!")
        if kwargs.get('pos'):
            self.pos = kwargs.get('pos')
        if kwargs.get('center'):
            self.pos = kwargs.get('center')

    """
    properties for enabling/diableing rotate/scale/translate
    """
    def _get_do_rotation(self):
        return self._do_rotation == 1
    def _set_do_rotation(self, flag):
        self._do_rotation = flag
    do_rotation = property(_get_do_rotation, _set_do_rotation, doc='Determines whether user interaction can rotate the widget')
    
    def _get_do_scale(self):
        return self._do_scale
    def _set_do_scale(self, flag):
        self._do_scale = flag
        if not self._do_scale:
            self.scale_max = self.scale_min = self.scale
    do_scale = property(_get_do_scale, _set_do_scale, doc='Determines whether user interaction can scale the widget')

    def _get_do_translation(self):
        return self._do_translation
    def _set_do_translation(self, val):
        self._do_translation = val
        self._do_translation_x = self._do_translation_y = 0.0
        if type(val) in (list, tuple, str):
            self._do_translation_x = 'x' in self.do_translation
            self._do_translation_y = 'y' in self.do_translation
        elif val:
            self._do_translation_x = self._do_translation_y = 1.0
    do_translation = property(_get_do_translation, _set_do_translation, doc='Determines whether user interaction can translate the widget')


    @property
    def bbox(self):
        '''
        Returns teh bounding box of teh widget in parent space
            returns "bbox":  ((x,y),(w,h))  x,y = lower left corner
        '''
        xmin, ymin = xmax, ymax = self.to_parent(0,0)
        for point in [(self.width,0),(0, self.height), self.size]:
            x,y = self.to_parent(*point)
            if x < xmin: xmin = x
            if y < ymin: ymin = y
            if x > xmax: xmax = x
            if y > ymax: ymax = y
        return (xmin, ymin), (xmax-xmin, ymax-ymin)

    def _get_center(self):
        return (self.bbox[0][0] + self.bbox[1][0]/2.0,
                self.bbox[0][1] + self.bbox[1][1]/2.0)
    def _set_center(self, center):
        if center == self.center:
            return False
        t = Vector(*center) - self.center
        trans = translation_matrix( (t.x, t.y, 0) )
        self.apply_transform(trans)
    center = property(_get_center, _set_center, doc='Object center (x, y).' )

    def _get_pos(self):
        return self.bbox[0]
    def _set_pos(self, pos):
        _pos = self.bbox[0]
        if pos == _pos:
            return
        t = Vector(*pos) - _pos
        trans = translation_matrix( (t.x, t.y, 0) )
        self.apply_transform(trans)
    pos = property(_get_pos, _set_pos, doc='Object position (x, y).  Lower left of bounding box for rotated scatter')

    def _get_x(self):
        return self.pos[0]
    def _set_x(self, x):
        if x == self.pos[0]:
            return False
        self.pos = (x, self.y)
        return True
    x = property(_get_x, _set_x, doc = 'Object X position')

    def _get_y(self):
        return self.pos[1]
    def _set_y(self, y):
        if y == self.pos[1]:
            return False
        self.pos = (self.x, y)
        return True
    y = property(_get_y, _set_y, doc = 'Object Y position')

    def _get_rotation(self):
        v1 = Vector(0,10)
        v2 = Vector(*self.to_parent(*self.pos)) - self.to_parent(self.x, self.y+10)
        return -1.0 *(v1.angle(v2) + 180) % 360
    def _set_rotation(self, rotation):
        angle_change = self.rotation - rotation
        r = rotation_matrix( -radians(angle_change), (0, 0, 1) )
        self.apply_transform(r ,post_multiply=True, anchor=self.to_local(*self.center))
    rotation = property(_get_rotation, _set_rotation, doc='''Get/set the rotation around center of the object (in degree)''')

    def _get_scale(self):
        p1 = Vector(*self.to_parent(0,0))
        p2 = Vector(*self.to_parent(1,0))
        scale = p1.distance(p2)
        return float(scale)
    def _set_scale(self, scale):
        #scale = boundary(scale, self.scale_min, self.scale_max) 
        rescale = scale * 1.0/self.scale
        self.apply_transform(scale_matrix(rescale), post_multiply=True, anchor=self.to_local(*self.center))
    scale = property(_get_scale, _set_scale, doc='''Get/set the scale factor of the object''')
    _scale = property(_get_scale, _set_scale, doc='''Get/set the scale factor of the object''')


    @deprecated
    def _get_transform_mat(self):
        '''Use transform_gl for an OpenGL transformation instead.'''
        return self._transform_gl
    transform_mat = property(_get_transform_mat,
        doc = "DEPRECATED Return the transformation matrix for OpenGL,  read only!'")

    def _get_transform_gl(self):
        return self._transform_gl
    transform_gl = property(_get_transform_gl,
        doc = " Return the transformation matrix for OpenGL,  read only!'")

    def _get_transform_inv_gl(self):
        return self._transform_inv_gl
    transform_inv_gl = property(_get_transform_inv_gl,
        doc = " Return the inverse transformation matrix for OpenGL,  read only!'")

    def _get_transform(self):
        return self._transform
    def _set_transform(self, x):
        self._transform = x
        self.update_matrices()
    transform = property(_get_transform, _set_transform,
        doc='Get/Set transformation matrix (numpy matrix)')

    def _get_transform_inv(self):
        return self._transform_inv
    transform_inv = property(_get_transform_inv,
        doc = "Inverse of transformation matrix (numpy matrix),  read only!'")

    def _get_state(self):
        return serialize_numpy(self._transform)
    def _set_state(self, state):
        self.transform = deserialize_numpy(state)
    state = property(
        lambda self: self._get_state(),
        lambda self, x: self._set_state(x),
        doc='Save/restore the state of matrix widget (require numpy)')


    def collide_point(self, x, y):
        if not self.visible:
            return False
        local_coords = self.to_local(x, y)
        if local_coords[0] > 0 and local_coords[0] < self.width \
           and local_coords[1] > 0 and local_coords[1] < self.height:
            return True
        else:
            return False
        
    def to_parent(self, x, y, **k):
        p = matrix_multiply(self._transform, (x,y,0,1))
        return (p[0],p[1])

    def to_local(self, x, y, **k):
        p = matrix_multiply(self._transform_inv, (x,y,0,1))
        return (p[0],p[1])

    
    def apply_angle_scale_trans(self, angle, scale, trans, point=Vector(0,0)):
        '''Update matrix transformation by adding new angle, scale and translate.

        :Parameters:
            `angle` : float
                Rotation angle to add
            `scale` : float
                Scaling value to add
            `trans` : Vector
                Vector translation to add
            `point` : Vector, default to (0, 0)
                Point to apply transformation
        '''
        old_scale = self.scale
        new_scale = old_scale * scale
        if new_scale < self.scale_min or old_scale > self.scale_max:
            scale = 1
        
        t = translation_matrix((trans[0]*self._do_translation_x, trans[1]*self._do_translation_y, 0))
        t = matrix_multiply(t, translation_matrix( (point[0], point[1], 0) ))
        t = matrix_multiply(t, rotation_matrix(angle, (0,0,1)) )
        t = matrix_multiply(t, scale_matrix(scale) )
        t = matrix_multiply(t, translation_matrix( (-point[0], -point[1], 0) ))
        self.apply_transform(t)
        
        self.dispatch_event('on_transform', None)

    def apply_transform(self, trans, post_multiply=False, anchor=(0,0)):
        '''
        transforms scatter by trans (on top of its current transformation state
        args:
            trans, transformation to be applied to teh scatter widget)
        optional args:
            anchor (default=None,same as (0,0)), the point to use as the origin of teh transformation (uses local widget space)
            post_multiply (default=False), if true the transform matrix is post multiplied (as if applied before the current transform)
        '''
        t = translation_matrix( (anchor[0], anchor[1], 0) )
        t = matrix_multiply(t, trans)
        t = matrix_multiply(t, translation_matrix( (-anchor[0], -anchor[1], 0) ))

        if post_multiply:
            self.transform = matrix_multiply(self._transform, t)
        else:
            self.transform = matrix_multiply(t, self._transform)

    def update_matrices(self):
        #update the inverse and OpenGL matrices
        self._transform_inv = inverse_matrix(self._transform)
        self._transform_gl = self._transform.T.tolist() #for openGL
        self._transform_inv_gl = self._transform.T.tolist() #for openGL


    def _apply_drag(self, touch):
        #_last_touch_pos has last pos in correct parent space, just liek incoming touch
        dx = (touch.x - self._last_touch_pos[touch][0]) * self._do_translation_x
        dy = (touch.y - self._last_touch_pos[touch][1]) * self._do_translation_y
        self.apply_transform( translation_matrix((dx,dy,0)) )

    def transform_with_touch(self, touch):
        #just do a simple one finger drag
        if len(self._touches) == 1:
            return self._apply_drag(touch)

        #we have more than one touch...
        points = [Vector(*self._last_touch_pos[t]) for t in self._touches] 

        #we only want to transform if the touch is part of the two touches furthest apart!
        #so firt we find anchor, the point to transform around as teh touch farthest away from touch
        anchor  = max(points, key=lambda p: p.distance(touch.pos))
                
        #now we find the touch farthest away from anchor, if its not teh same as touch
        #touch is not one of teh two touches used to transform
        farthest = max(points, key=lambda p: anchor.distance(p))
        if points.index(farthest) != self._touches.index(touch):
            return

        #ok, so we have touch, and anchor, so we can actually compute the transformation        
        old_line = Vector(*touch.dpos) - anchor
        new_line = Vector(*touch.pos) - anchor
        
        angle = radians( new_line.angle(old_line) ) * self._do_rotation
        scale = new_line.length()/old_line.length()
        new_scale = scale*self.scale
        if new_scale < self.scale_min or new_scale > self.scale_max:
            scale = 1.0
        
        self.apply_transform(rotation_matrix(angle,(0,0,1)), anchor=anchor)
        self.apply_transform(scale_matrix(scale), anchor=anchor)
        
        #dispatch on_transform with th touch that caused it
        self.dispatch_event('on_transform', touch)

    def on_transform(self, touch):
        pass

    def on_touch_down(self, touch):
        x, y = touch.x, touch.y
        # if the touch isnt on the widget we do nothing
        if not self.collide_point(x, y):
            return False
        
        # let the child widgets handle the event if they want
        touch.push(attrs=['x','y','dxpos','dypos'])
        touch.x, touch.y = self.to_local(x, y)
        touch.dxpos, touch.dypos = self.to_local(touch.dxpos, touch.dypos)
        if super(MTScatter, self).on_touch_down(touch):
            touch.pop()
            return True
        touch.pop()

        #grab the touch so we get all it later move events for sure
        touch.grab(self)
        self._last_touch_pos[touch] = touch.pos
        self._touches.append(touch)

        #bring to front if auto_bring to front is on
        if self.auto_bring_to_front:
            self.bring_to_front()
        return True

    def on_touch_move(self, touch):
        x, y = touch.x, touch.y
        # let the child widgets handle the event if they want
        if self.collide_point(x, y) and not touch.grab_current == self:
            touch.push(attrs=['x','y','dxpos','dypos'])
            touch.x, touch.y = self.to_local(x, y)
            touch.dxpos, touch.dypos = self.to_local(touch.dxpos, touch.dypos)
            if super(MTScatter, self).on_touch_move(touch):
                touch.pop()
                return True
            touch.pop()

        # rotate/scale/translate
        if touch in self._touches and touch.grab_current == self:
            self.transform_with_touch (touch)
            self._last_touch_pos[touch] = touch.pos

        # stop porpagating if its within our bounds
        if self.collide_point(x, y):
            return True

    def on_touch_up(self, touch):
        x, y = touch.x, touch.y
        # if the touch isnt on the widget we do nothing, just try children
        if not touch.grab_current == self:
            touch.push(attrs=['x','y','dxpos','dypos'])
            touch.x, touch.y = self.to_local(x, y)
            touch.dxpos, touch.dypos = self.to_local(touch.dxpos, touch.dypos)
            if super(MTScatter, self).on_touch_up(touch):
                touch.pop()
                return True
            touch.pop()
   
        # remove it from our saved touches
        if touch in self._touches and touch.grab_state:
            touch.ungrab(self)
            del self._last_touch_pos[touch]
            self._touches.remove(touch)

        # stop porpagating if its within our bounds
        if self.collide_point(x, y):
            return True

    def on_draw(self):
        if not self.visible:
            return
        with gx_matrix:
            glMultMatrixf(self._transform_gl)
            super(MTScatter, self).on_draw()

    def draw(self):
        set_color(*self.style['bg-color'])
        drawCSSRectangle((0,0), (self.width, self.height), style=self.style)


class MTScatterWidget(MTScatter):
    '''This class is deprecated, you should use MTScatter now.'''
    pass


class MTScatterPlane(MTScatterWidget):
    '''A Plane that transforms for zoom/rotate/pan.
    if none of the childwidgets handles the input
    (the background is touched), all of them are transformed
    together
    '''
    def __init__(self, **kwargs):
        kwargs.setdefault('auto_bring_to_front', False)
        super(MTScatterPlane, self).__init__(**kwargs)

    def draw(self):
        pass

    def collide_point(self, x, y):
        return self.visible


class MTScatterImage(MTScatterWidget):
    '''MTScatterImage is a image showed in a Scatter widget

    :Parameters:
        `filename` : str
            Filename of image
        `image` : Image
            Instead of using filename, use a Image object
        `opacity` : float, default to 1.0
            Used to set the opacity of the image.
        `scale` : float, default is 1.0
            Scaling of image, default is 100%, ie 1.0
    '''
    def __init__(self, **kwargs):
        kwargs.setdefault('filename', None)
        kwargs.setdefault('opacity', 1.0)
        kwargs.setdefault('scale', 1.0)
        kwargs.setdefault('image', None)
        if kwargs.get('filename') is None and kwargs.get('image') is None:
            raise Exception('No filename or image given to MTScatterImage')

        super(MTScatterImage, self).__init__(**kwargs)
        self.image          = kwargs.get('image')
        self.scale          = kwargs.get('scale')
        self.filename       = kwargs.get('filename')
        self.opacity        = kwargs.get('opacity')
        self.size           = self.image.size

    def _get_filename(self):
        return self._filename
    def _set_filename(self, filename):
        self._filename = filename
        if filename:
            self.image = pymt.Image(self.filename)
    filename = property(_get_filename, _set_filename)

    def draw(self):
        self.size           = self.image.size
        self.image.opacity  = self.opacity
        self.image.draw()

class MTScatterSvg(MTScatterWidget):
    '''Render an svg image into a scatter widget

    :Parameters:
        `filename` : str
            Filename of image
        `rawdata` : str
            Raw data of the image. If given, the filename property is used only for cache purposes.
    '''
    def __init__(self, **kwargs):
        kwargs.setdefault('filename', None)
        if kwargs.get('filename') is None:
            raise Exception('No filename given to MTSvg')
        kwargs.setdefault('rawdata', None)
        super(MTScatterSvg, self).__init__(**kwargs)
        self.squirt = MTSvg(filename=kwargs.get('filename'), rawdata=kwargs.get('rawdata'))
        self.size = (self.squirt.svg.width, self.squirt.svg.height)

    def draw(self):
        self.squirt.draw()

MTWidgetFactory.register('MTScatterImage', MTScatterImage)
MTWidgetFactory.register('MTScatterPlane', MTScatterPlane)
MTWidgetFactory.register('MTScatterSvg', MTScatterSvg)
MTWidgetFactory.register('MTScatterWidget', MTScatterWidget)
