# helper.py
# Copyright (C) 2008, 2009 Michael Trier (mtrier@gmail.com) and contributors
#
# This module is part of GitPython and is released under
# the BSD License: http://www.opensource.org/licenses/bsd-license.php

import os
import sys
from git import Repo, Remote, GitCommandError
from unittest import TestCase
import tempfile
import shutil
import cStringIO

GIT_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

__all__ = (
    'fixture_path', 'fixture', 'absolute_project_path', 'StringProcessAdapter',
    'with_rw_repo', 'with_rw_and_rw_remote_repo', 'TestBase', 'TestCase', 'GIT_REPO'
)

#{ Routines


def fixture_path(name):
    test_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(test_dir, "fixtures", name)


def fixture(name):
    return open(fixture_path(name), 'rb').read()


def absolute_project_path():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#} END routines

#{ Adapters


class StringProcessAdapter(object):

    """Allows to use strings as Process object as returned by SubProcess.Popen.
    Its tailored to work with the test system only"""

    def __init__(self, input_string):
        self.stdout = cStringIO.StringIO(input_string)
        self.stderr = cStringIO.StringIO()

    def wait(self):
        return 0

    poll = wait

#} END adapters

#{ Decorators


def _mktemp(*args):
    """Wrapper around default tempfile.mktemp to fix an osx issue"""
    tdir = tempfile.mktemp(*args)
    if sys.platform == 'darwin':
        tdir = '/private' + tdir
    return tdir


def _rmtree_onerror(osremove, fullpath, exec_info):
    """
    Handle the case on windows that read-only files cannot be deleted by 
    os.remove by setting it to mode 777, then retry deletion.
    """
    if os.name != 'nt' or osremove is not os.remove:
        raise

    os.chmod(fullpath, 0777)
    os.remove(fullpath)


def with_rw_repo(working_tree_ref, bare=False):
    """
    Same as with_bare_repo, but clones the rorepo as non-bare repository, checking 
    out the working tree at the given working_tree_ref.

    This repository type is more costly due to the working copy checkout.

    To make working with relative paths easier, the cwd will be set to the working 
    dir of the repository.
    """
    assert isinstance(working_tree_ref, basestring), "Decorator requires ref name for working tree checkout"

    def argument_passer(func):
        def repo_creator(self):
            prefix = 'non_'
            if bare:
                prefix = ''
            # END handle prefix
            repo_dir = _mktemp("%sbare_%s" % (prefix, func.__name__))
            rw_repo = self.rorepo.clone(repo_dir, shared=True, bare=bare, n=True)

            rw_repo.head.commit = rw_repo.commit(working_tree_ref)
            if not bare:
                rw_repo.head.reference.checkout()
            # END handle checkout

            prev_cwd = os.getcwd()
            os.chdir(rw_repo.working_dir)
            try:
                try:
                    return func(self, rw_repo)
                except:
                    print >> sys.stderr, "Keeping repo after failure: %s" % repo_dir
                    repo_dir = None
                    raise
            finally:
                os.chdir(prev_cwd)
                rw_repo.git.clear_cache()
                if repo_dir is not None:
                    shutil.rmtree(repo_dir, onerror=_rmtree_onerror)
                # END rm test repo if possible
            # END cleanup
        # END rw repo creator
        repo_creator.__name__ = func.__name__
        return repo_creator
    # END argument passer
    return argument_passer


def with_rw_and_rw_remote_repo(working_tree_ref):
    """
    Same as with_rw_repo, but also provides a writable remote repository from which the
    rw_repo has been forked as well as a handle for a git-daemon that may be started to 
    run the remote_repo.
    The remote repository was cloned as bare repository from the rorepo, wheras 
    the rw repo has a working tree and was cloned from the remote repository.

    remote_repo has two remotes: origin and daemon_origin. One uses a local url, 
    the other uses a server url. The daemon setup must be done on system level 
    and should be an inetd service that serves tempdir.gettempdir() and all 
    directories in it.

    The following scetch demonstrates this::
     rorepo ---<bare clone>---> rw_remote_repo ---<clone>---> rw_repo

    The test case needs to support the following signature::
        def case(self, rw_repo, rw_remote_repo)

    This setup allows you to test push and pull scenarios and hooks nicely.

    See working dir info in with_rw_repo
    """
    assert isinstance(working_tree_ref, basestring), "Decorator requires ref name for working tree checkout"

    def argument_passer(func):
        def remote_repo_creator(self):
            remote_repo_dir = _mktemp("remote_repo_%s" % func.__name__)
            repo_dir = _mktemp("remote_clone_non_bare_repo")

            rw_remote_repo = self.rorepo.clone(remote_repo_dir, shared=True, bare=True)
            # recursive alternates info ?
            rw_repo = rw_remote_repo.clone(repo_dir, shared=True, bare=False, n=True)
            rw_repo.head.commit = working_tree_ref
            rw_repo.head.reference.checkout()

            # prepare for git-daemon
            rw_remote_repo.daemon_export = True

            # this thing is just annoying !
            crw = rw_remote_repo.config_writer()
            section = "daemon"
            try:
                crw.add_section(section)
            except Exception:
                pass
            crw.set(section, "receivepack", True)
            # release lock
            del(crw)

            # initialize the remote - first do it as local remote and pull, then
            # we change the url to point to the daemon. The daemon should be started
            # by the user, not by us
            d_remote = Remote.create(rw_repo, "daemon_origin", remote_repo_dir)
            d_remote.fetch()
            remote_repo_url = "git://localhost%s" % remote_repo_dir

            d_remote.config_writer.set('url', remote_repo_url)

            # try to list remotes to diagnoes whether the server is up
            try:
                rw_repo.git.ls_remote(d_remote)
            except GitCommandError, e:
                print str(e)
                if os.name == 'nt':
                    raise AssertionError(
                        'git-daemon needs to run this test, but windows does not have one. Otherwise, run: git-daemon "%s"' % os.path.dirname(_mktemp()))
                else:
                    raise AssertionError(
                        'Please start a git-daemon to run this test, execute: git-daemon "%s"' % os.path.dirname(_mktemp()))
                # END make assertion
            # END catch ls remote error

            # adjust working dir
            prev_cwd = os.getcwd()
            os.chdir(rw_repo.working_dir)
            try:
                return func(self, rw_repo, rw_remote_repo)
            finally:
                os.chdir(prev_cwd)
                rw_repo.git.clear_cache()
                rw_remote_repo.git.clear_cache()
                shutil.rmtree(repo_dir, onerror=_rmtree_onerror)
                shutil.rmtree(remote_repo_dir, onerror=_rmtree_onerror)
            # END cleanup
        # END bare repo creator
        remote_repo_creator.__name__ = func.__name__
        return remote_repo_creator
        # END remote repo creator
    # END argument parsser

    return argument_passer

#} END decorators


class TestBase(TestCase):

    """
    Base Class providing default functionality to all tests such as:

    - Utility functions provided by the TestCase base of the unittest method such as::
        self.fail("todo")
        self.failUnlessRaises(...)

    - Class level repository which is considered read-only as it is shared among 
      all test cases in your type.
      Access it using:: 
       self.rorepo  # 'ro' stands for read-only

      The rorepo is in fact your current project's git repo. If you refer to specific 
      shas for your objects, be sure you choose some that are part of the immutable portion 
      of the project history ( to assure tests don't fail for others ).
    """

    @classmethod
    def setUp(cls):
        """
        Dynamically add a read-only repository to our actual type. This way 
        each test type has its own repository
        """
        cls.rorepo = Repo(GIT_REPO)

    def _make_file(self, rela_path, data, repo=None):
        """
        Create a file at the given path relative to our repository, filled
        with the given data. Returns absolute path to created file.
        """
        repo = repo or self.rorepo
        abs_path = os.path.join(repo.working_tree_dir, rela_path)
        fp = open(abs_path, "w")
        fp.write(data)
        fp.close()
        return abs_path
