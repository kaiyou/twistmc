""" Twistmc (Twist my components!) is a Twisted-based Python library.

The main purpose of Twistmc is to help programmers implement complex
component-based Twisted applications without relying on Twisted service
hierarchy. Service hierarchy is great, however somehow limited to the
extent of constraining large applications and imposing monolythic
designs.

Twistmc grants the programmer with a simple syntax for semantically
describe complex dependencies between application components and
masks all the internals of aynchronous dependency management.
"""

import inspect
import functools

#: Base depth to the declared class frame.
DEPTH = 2

#: Name of the setup method list.
SETUP = "__twistmc_setup__"


def _set(depth, key, value):
    """ Set a class local.
    """
    frame = inspect.currentframe(depth)
    frame.f_locals[key] = value


def _get(depth, key):
    """ Get a class local.
    """
    frame = inspect.currentframe(depth)
    return frame.f_locals[key] if key in frame.f_locals else None


def _install(depth):
    """ Install component-related metadata into the class being declared.
    """
    # Replace the class metaclass with our component implementation, so
    # that the class is automagically populated with necessary attributes
    # for dependency management.
    if not _get(depth + 1, '__metaclass__'):
        _set(depth + 1, '__metaclass__', metaclass)
    # Set some default values for setup and teardon methods.
    if not _get(depth + 1, SETUP):
        _set(depth + 1, SETUP, list())


def plugin(function, *args, **kargs):
    """ Plug an object inside a component.

    Components do not have to be declared as such. Any class that
    declares a class attribute by using plugin() is implicitly
    declared as a Twistmc component. Thus, components may inherit from
    usual Twisted classes or anything else.
    """
    # Install if not yet.
    _install(DEPTH)
    # Plugins are actually instanciated whenever the class itself is
    # instanciated.
    return Plugin(function, *args, **kwargs)


def setup(function):
    """ Method decorator to declare a setup method.

    The setup methods are called once every dependency is cleared. They
    may return a Twisted deferred object in order for components
    depending on this one to wait until setup is actually complete.

    ..warning::

        There is no guarantee that setup methods are invoked in the
        same order as declared. They may even run all a once if built
        on the asynchronous paradigm.
    """
    # Install if not yet.
    _install(DEPTH)
    # Append a setup method
    _get(DEPTH, SETUP).append(function)


def component(clazz):
    """ Class decorator for explicit component declaration.
    """
    # We ultimately simply aim at re-defining the initialization
    # method for easy instance interception.
    clazz.__init__ = functools.partial(init_replacement, clazz.__init__, clazz)
    return clazz


def metaclass(classname, parents, attributes):
    """ Metaclass for every implicit component.
    """
    # Simply call the class decorator.
    return component(type(classname, parents, attributes))


def init_replacement(self, init, clazz, *args, **kwargs):
    """ Replacement method for the initialization of components.
    """
    init(self)
    for key, value in clazz.__dict__:
        if type(value) is Plugin:
            pass


class Plugin(object):
    """ Implementation of the property protocol for plugin attributes.
    """

    def __init__(self, function, *args, **kwargs):
        self._data = (function, args, kwargs)
