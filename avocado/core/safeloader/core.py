import ast
import collections
import os
import sys
from importlib.machinery import PathFinder

from .docstring import (check_docstring_directive, get_docstring_directives,
                        get_docstring_directives_requirements,
                        get_docstring_directives_tags)
from .module import PythonModule


def get_methods_info(statement_body, class_tags, class_requirements):
    """Returns information on test methods.

    :param statement_body: the body of a "class" statement
    :param class_tags: the tags at the class level, to be combined with the
                       tags at the method level.
    :param class_requirements: the requirements at the class level, to be
                               combined with the requirements at the method
                               level.
    """
    methods_info = []
    for st in statement_body:
        if (isinstance(st, ast.FunctionDef) and
                st.name.startswith('test')):
            docstring = ast.get_docstring(st)

            mt_tags = get_docstring_directives_tags(docstring)
            mt_tags.update(class_tags)

            mt_requirements = get_docstring_directives_requirements(docstring)
            mt_requirements.extend(class_requirements)

            methods = [method for method, _, _ in methods_info]
            if st.name not in methods:
                methods_info.append((st.name, mt_tags, mt_requirements))

    return methods_info


def _extend_test_list(current, new):
    for test in new:
        test_method_name = test[0]
        if test_method_name not in [_[0] for _ in current]:
            current.append(test)


def _examine_same_module(parents, info, disabled, match, module,
                         target_module, target_class, determine_match):
    # Searching the parents in the same module
    for parent in parents[:]:
        # Looking for a 'class FooTest(Parent)'
        if not isinstance(parent, ast.Name):
            # 'class FooTest(bar.Bar)' not supported withing
            # a module
            continue
        parent_class = parent.id

        # From this point we use `_$variable` to name temporary returns
        # from method calls that are to-be-assigned/combined with the
        # existing `$variable`.
        _info, _disable, _match = _examine_class(target_module,
                                                 target_class,
                                                 determine_match,
                                                 module.path,
                                                 parent_class,
                                                 match)
        if _info:
            parents.remove(parent)
            _extend_test_list(info, _info)
            disabled.update(_disable)
        if _match is not match:
            match = _match

    return match


class ClassNotSuitable(Exception):
    """Exception raised when examination of a class should not proceed."""


def _get_attributes_for_further_examination(parent, module):
    """Returns path, module and class for further examination."""
    if hasattr(parent, 'value'):
        if hasattr(parent.value, 'id'):
            # We know 'parent.Class' or 'asparent.Class' and need
            # to get path and original_module_name. Class is given
            # by parent definition.
            _parent = module.imported_objects.get(parent.value.id)
            if _parent is None:
                # We can't examine this parent (probably broken
                # module)
                raise ClassNotSuitable
            parent_path = os.path.dirname(_parent)
            parent_module = os.path.basename(_parent)
            parent_class = parent.attr
        else:
            # We don't support multi-level 'parent.parent.Class'
            raise ClassNotSuitable
    else:
        # We only know 'Class' or 'AsClass' and need to get
        # path, module and original class_name
        _parent = module.imported_objects.get(parent.id)
        if _parent is None:
            # We can't examine this parent (probably broken
            # module)
            raise ClassNotSuitable
        parent_path, parent_module, parent_class = (
            _parent.rsplit(os.path.sep, 2))

    return parent_path, parent_module, parent_class


def _examine_class(target_module, target_class, determine_match, path,
                   class_name, match):
    """
    Examine a class from a given path

    :param target_module: the name of the module from which a class should
                          have come from.  When attempting to find a Python
                          unittest, the target_module will most probably
                          be "unittest", as per the standard library module
                          name.  When attempting to find Avocado tests, the
                          target_module will most probably be "avocado".
    :type target_module: str
    :param target_class: the name of the class that is considered to contain
                         test methods.  When attempting to find Python
                         unittests, the target_class will most probably be
                         "TestCase".  When attempting to find Avocado tests,
                         the target_class  will most probably be "Test".
    :type target_class: str
    :param determine_match: a callable that will determine if a match has
                            occurred or not
    :type determine_match: function
    :param path: path to a Python source code file
    :type path: str
    :param class_name: the specific class to be found
    :type path: str
    :param match: whether the inheritance from <target_module.target_class> has
                  been determined or not
    :type match: bool
    :returns: tuple where first item is a list of test methods detected
              for given class; second item is set of class names which
              look like avocado tests but are force-disabled.
    :rtype: tuple
    """
    module = PythonModule(path, target_module, target_class)
    info = []
    disabled = set()

    for klass in module.iter_classes():
        if class_name != klass.name:
            continue

        docstring = ast.get_docstring(klass)

        if match is False:
            match = determine_match(module, klass, docstring)

        info = get_methods_info(klass.body,
                                get_docstring_directives_tags(docstring),
                                get_docstring_directives_requirements(
                                    docstring))

        # Getting the list of parents of the current class
        parents = klass.bases

        match = _examine_same_module(parents, info, disabled, match, module,
                                     target_module, target_class, determine_match)

        # If there are parents left to be discovered, they
        # might be in a different module.
        for parent in parents:
            try:
                (parent_path,
                 parent_module,
                 parent_class) = _get_attributes_for_further_examination(parent,
                                                                         module)
            except ClassNotSuitable:
                continue

            modules_paths = [parent_path,
                             os.path.dirname(module.path)] + sys.path
            found_spec = PathFinder.find_spec(parent_module, modules_paths)
            if found_spec is None:
                continue
            _info, _disabled, _match = _examine_class(target_module,
                                                      target_class,
                                                      determine_match,
                                                      found_spec.origin,
                                                      parent_class,
                                                      match)
            if _info:
                _extend_test_list(info, _info)
                disabled.update(_disabled)
            if _match is not match:
                match = _match

    return info, disabled, match


def find_python_tests(target_module, target_class, determine_match, path):
    """
    Attempts to find Python tests from source files

    A Python test in this context is a method within a specific type
    of class (or that inherits from a specific class).

    :param target_module: the name of the module from which a class should
                          have come from.  When attempting to find a Python
                          unittest, the target_module will most probably
                          be "unittest", as per the standard library module
                          name.  When attempting to find Avocado tests, the
                          target_module will most probably be "avocado".
    :type target_module: str
    :param target_class: the name of the class that is considered to contain
                         test methods.  When attempting to find Python
                         unittests, the target_class will most probably be
                         "TestCase".  When attempting to find Avocado tests,
                         the target_class  will most probably be "Test".
    :type target_class: str
    :type determine_match: a callable that will determine if a given module
                           and class is contains valid Python tests
    :type determine_match: function
    :param path: path to a Python source code file
    :type path: str
    :returns: tuple where first item is dict with class name and additional
              info such as method names and tags; the second item is
              set of class names which look like Python tests but have been
              forcefully disabled.
    :rtype: tuple
    """
    module = PythonModule(path, target_module, target_class)
    # The resulting test classes
    result = collections.OrderedDict()
    disabled = set()

    for klass in module.iter_classes():
        docstring = ast.get_docstring(klass)
        # Looking for a class that has in the docstring either
        # ":avocado: enable" or ":avocado: disable
        if check_docstring_directive(docstring, 'disable'):
            disabled.add(klass.name)
            continue

        if check_docstring_directive(docstring, 'enable'):
            info = get_methods_info(klass.body,
                                    get_docstring_directives_tags(docstring),
                                    get_docstring_directives_requirements(
                                        docstring))
            result[klass.name] = info
            continue

        # From this point onwards we want to do recursive discovery, but
        # for now we don't know whether it is avocado.Test inherited
        # (Ifs are optimized for readability, not speed)

        # If "recursive" tag is specified, it is forced as test
        if check_docstring_directive(docstring, 'recursive'):
            match = True
        else:
            match = module.is_matching_klass(klass)
        info = get_methods_info(klass.body,
                                get_docstring_directives_tags(docstring),
                                get_docstring_directives_requirements(
                                    docstring))
        # Getting the list of parents of the current class
        parents = klass.bases

        match = _examine_same_module(parents, info, disabled, match, module,
                                     target_module, target_class, determine_match)

        # If there are parents left to be discovered, they
        # might be in a different module.
        for parent in parents:
            try:
                (parent_path,
                 parent_module,
                 parent_class) = _get_attributes_for_further_examination(parent,
                                                                         module)
            except ClassNotSuitable:
                continue

            modules_paths = [parent_path,
                             os.path.dirname(module.path)] + sys.path
            found_spec = PathFinder.find_spec(parent_module, modules_paths)
            if found_spec is None:
                continue
            _info, _dis, _match = _examine_class(target_module,
                                                 target_class,
                                                 determine_match,
                                                 found_spec.origin,
                                                 parent_class,
                                                 match)
            if _info:
                info.extend(_info)
                disabled.update(_dis)
            if _match is not match:
                match = _match

        # Only update the results if this was detected as 'avocado.Test'
        if match:
            result[klass.name] = info
            disabled.update(disabled)

    return result, disabled


def _determine_match_avocado(module, klass, docstring):
    """
    Implements the match check for Avocado Instrumented Tests
    """
    directives = get_docstring_directives(docstring)
    if 'disable' in directives:
        return True
    if 'enable' in directives:
        return True
    if 'recursive' in directives:
        return True
    # Still not decided, try inheritance
    return module.is_matching_klass(klass)


def find_avocado_tests(path):
    return find_python_tests('avocado', 'Test', _determine_match_avocado, path)


def _determine_match_unittest(module, klass,
                              docstring):  # pylint: disable=W0613
    """
    Implements the match check for Python Unittests
    """
    return module.is_matching_klass(klass)


def find_python_unittests(path):
    found, _ = find_python_tests('unittest', 'TestCase',
                                 _determine_match_unittest,
                                 path)
    return found