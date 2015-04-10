import os
import sys
from functools import partial

from spec import eq_, skip, Spec, raises, ok_, trap
from mock import patch, Mock

from invoke import run
from invoke.config import Config
from invoke.context import Context
from invoke.runners import Runner, Local
from invoke.exceptions import Failure
from invoke.platform import WINDOWS

from _utils import support, reset_cwd, skip_if_windows, mock_subprocess


# Get the right platform-specific directory separator,
# because Windows command parsing doesn't like '/'
error_command = "{0} err.py".format(sys.executable)

def _run(returns=None, **kwargs):
    """
    Create a Runner w/ retval reflecting ``returns`` & call ``run(**kwargs)``.
    """
    # Set up return value tuple for Runner.run_direct
    returns = returns or {}
    returns.setdefault('exited', 0)
    value = map(
        lambda x: returns.get(x, None),
        ('stdout', 'stderr', 'exited', 'exception'),
    )
    class MockRunner(Runner):
        def run_direct(self, command, **kwargs):
            return value
    return MockRunner(Context()).run("whatever", **kwargs)


def _runner(key, config_val):
    # Config reflecting given data
    c = Context(config=Config(overrides={'run': {key: config_val}}))
    # Runner w/ methods mocked for inspection (& to prevent actual subprocess)
    r = Runner(context=c)
    r.run_direct = Mock(return_value=("", "", 0, None))
    r.run_direct.__name__ = 'run_direct'
    return r

def _config_check(key, config_val, kwarg_val, expected, func='run_direct'):
    r = _runner(key=key, config_val=config_val)
    # NOTE: mocking select_method this way means result pty info is incorrect.
    # Doesn't matter for these tests.
    r.select_method = Mock(return_value=r.run_direct)
    kwargs = {}
    if kwarg_val is not None:
        kwargs[key] = kwarg_val
    r.run('whatever', **{key: kwarg_val})
    eq_(getattr(r, func).call_args[1][key], expected)


# Shorthand for mocking Local.run_pty so tests run under non-pty environments
# don't asplode.
_patch_run_pty = patch.object(
    Local,
    'run_pty',
    return_value=('', '', 0, None),
    func_name='run_pty',
    __name__='run_pty',
)

# Shorthand for mocking os.isatty
_is_tty = patch('os.isatty', return_value=True)
_not_tty = patch('os.isatty', return_value=False)


class Run(Spec):
    "Basic run() / Context.run() behavior"

    def setup(self):
        os.chdir(support)
        self.both = "echo foo && {0} bar".format(error_command)
        self.out = "echo foo"
        self.err = "{0} bar".format(error_command)
        self.sub = "inv -c pty_output hide_{0}"

    def teardown(self):
        reset_cwd()

    class init:
        "__init__"
        def takes_a_context_instance(self):
            c = Context()
            eq_(Runner(c).context, c)

        @raises(TypeError)
        def context_instance_is_required(self):
            Runner()

    class uses_context_config:
        "Context's config values are honored"

        def warn(self):
            check = partial(_config_check, key='warn', config_val='yup')
            check(kwarg_val=None, expected='yup')
            check(kwarg_val='nope', expected='nope')

        def hide(self):
            check = partial(_config_check, key='hide', config_val='stdout')
            # NOTE: expected are post-normalized values
            check(kwarg_val=None, expected=('out',))
            check(kwarg_val='stderr', expected=('err',))

        def pty(self):
            check = partial(
                _config_check, key='pty', config_val=True, func='select_method'
            )
            check(kwarg_val=None, expected=True)
            check(kwarg_val=False, expected=False)

        @trap
        def echo(self):
            # honors default
            r = _runner(key='echo', config_val=True)
            r.run('whatever')
            ok_('whatever' in sys.stdout.getvalue())
            # kwarg overrides

        def fallback(self):
            check = partial(
                _config_check, key='fallback', config_val=True,
                func='select_method'
            )
            check(kwarg_val=None, expected=True)
            check(kwarg_val=False, expected=False)

        def encoding(self):
            check = partial(_config_check, key='encoding', config_val='UTF-7')
            check(kwarg_val=None, expected='UTF-7')
            check(kwarg_val='UTF-9', expected='UTF-9')

    class return_value:
        def return_code_in_result(self):
            """
            Result has .return_code (and .exited) containing exit code int
            """
            r = run(self.out, hide='both')
            eq_(r.return_code, 0)
            eq_(r.exited, 0)

        def nonzero_return_code_for_failures(self):
            result = run("false", warn=True)
            eq_(result.exited, 1)
            result = run("goobypls", warn=True, hide='both')
            expected = 1 if WINDOWS else 127
            eq_(result.exited, expected)

        def stdout_attribute_contains_stdout(self):
            eq_(run(self.out, hide='both').stdout, 'foo\n')

        def stderr_attribute_contains_stderr(self):
            eq_(run(self.err, hide='both').stderr, 'bar\n')

        def ok_attr_indicates_success(self):
            eq_(_run().ok, True)
            eq_(_run(returns={'exited': 1}, warn=True).ok, False)

        def failed_attr_indicates_failure(self):
            eq_(_run().failed, False)
            eq_(_run(returns={'exited': 1}, warn=True).failed, True)

        def has_exception_attr(self):
            eq_(_run().exception, None)

    class failure_handling:
        @raises(Failure)
        def fast_failures(self):
            run("false")

        def run_acts_as_success_boolean(self):
            ok_(not run("false", warn=True))
            ok_(run("true"))

        def non_one_return_codes_still_act_as_False(self):
            ok_(not run("goobypls", warn=True, hide='both'))

        def warn_kwarg_allows_continuing_past_failures(self):
            eq_(run("false", warn=True).exited, 1)

        def Failure_repr_includes_stderr(self):
            try:
                run("{0} ohnoz && exit 1".format(error_command), hide='both')
                assert false # noqa. Ensure failure to Failure fails
            except Failure as f:
                r = repr(f)
                err = "Sentinel 'ohnoz' not found in {0!r}".format(r)
                assert 'ohnoz' in r, err

    class output_controls:
        @trap
        def _hide_both(self, val):
            run(self.both, hide=val)
            eq_(sys.stdall.getvalue(), "")

        def hide_both_hides_everything(self):
            self._hide_both('both')

        def hide_True_hides_everything(self):
            self._hide_both(True)

        @trap
        def hide_out_only_hides_stdout(self):
            run(self.both, hide='out')
            eq_(sys.stdout.getvalue().strip(), "")
            eq_(sys.stderr.getvalue().strip(), "bar")

        @trap
        def hide_err_only_hides_stderr(self):
            run(self.both, hide='err')
            eq_(sys.stdout.getvalue().strip(), "foo")
            eq_(sys.stderr.getvalue().strip(), "")

        @trap
        def hide_accepts_stderr_alias_for_err(self):
            run(self.both, hide='stderr')
            eq_(sys.stdout.getvalue().strip(), "foo")
            eq_(sys.stderr.getvalue().strip(), "")

        @trap
        def hide_accepts_stdout_alias_for_out(self):
            run(self.both, hide='stdout')
            eq_(sys.stdout.getvalue().strip(), "")
            eq_(sys.stderr.getvalue().strip(), "bar")

        @skip_if_windows
        @trap
        @mock_subprocess(stdout="boo", stderr="urns")
        def hide_both_hides_both_under_pty(self):
            # What was the old test doing? it was running 'echo <shit to stdout
            # and/or stderr>' in a subproce w/ pty, then running THAT in a
            # non-pty run() for some reason. Maybe because can't easily mock
            # pexpect? But should be able to do same as what I did for the
            # broken version of this test? (At least, I can apply it to all the
            # non pty tests...)
            run("whatever", hide='both', pty=True)
            for stream in sys.stdout, sys.stderr:
                eq_(stream.getvalue().strip(), "")

        @skip_if_windows
        def hide_out_hides_both_under_pty(self):
            r = run(self.sub.format('out', hide='both'))
            eq_(r.stdout, "")
            eq_(r.stderr, "")

        @skip_if_windows
        @trap
        def hide_err_has_no_effect_under_pty(self):
            r = run(self.sub.format('err', hide='both'))
            eq_(r.stdout, "foo\r\nbar\r\n")
            eq_(r.stderr, "")

        @trap
        def _no_hiding(self, val):
            run(self.both, hide=val)
            eq_(sys.stdout.getvalue().strip(), "foo")
            eq_(sys.stderr.getvalue().strip(), "bar")

        def hide_None_hides_nothing(self):
            self._no_hiding(None)

        def hide_False_hides_nothing(self):
            self._no_hiding(False)

        @raises(ValueError)
        def hide_unknown_vals_raises_ValueError(self):
            run("command", hide="what")

        def hide_unknown_vals_mention_value_given_in_error(self):
            value = "penguinmints"
            try:
                run("command", hide=value)
            except ValueError as e:
                msg = "Error from run(hide=xxx) did not tell user what the bad value was!" # noqa
                msg += "\nException msg: {0}".format(e)
                ok_(value in str(e), msg)
            else:
                assert False, "run() did not raise ValueError for bad hide= value" # noqa

        def hide_does_not_affect_capturing(self):
            eq_(run(self.out, hide='both').stdout, 'foo\n')

    class pseudo_terminals:
        @trap
        def return_value_indicates_whether_pty_was_used(self, *mocks):
            eq_(run("true").pty, False)
            if not WINDOWS:
                eq_(run("true", pty=True).pty, True)

        def pty_defaults_to_off(self):
            eq_(run("true").pty, False)

        @skip_if_windows # Not sure how to make this work on Windows
        def complex_nesting_doesnt_break(self):
            # GH issue 191
            substr = "      hello\t\t\nworld with spaces"
            cmd = """ eval 'echo "{0}" ' """.format(substr)
            # TODO: consider just mocking os.execv here (and in the other
            # tests) though that feels like too much of a tautology / testing
            # pexpect
            expected = '      hello\t\t\r\nworld with spaces\r\n'
            eq_(run(cmd, pty=True, hide='both').stdout, expected)

        @_not_tty
        @trap
        def pty_falls_back_to_off_if_True_and_not_isatty(self, *mocks):
            if WINDOWS:
                # Straight up "it shouldn't kaboom, it should fall back"
                run("true", pty=True)
            else:
                # Force termios to kaboom to trigger fallback
                import termios
                with patch('tty.tcgetattr', side_effect=termios.error):
                    run("true", pty=True)

        @_not_tty
        @trap
        def fallback_affects_result_pty_value(self, *mocks):
            eq_(run("true", pty=True).pty, False)

        @_not_tty
        @_patch_run_pty
        def fallback_can_be_overridden(self, run_pty, isatty):
            run("true", pty=True, fallback=False)
            assert run_pty.called

        # Force our test for pty-ness to fail
        @_not_tty
        @_patch_run_pty
        def overridden_fallback_affects_result_pty_value(self, *mocks):
            eq_(run("true", pty=True, fallback=False).pty, True)

    class command_echo:
        @trap
        def does_not_echo_commands_run_by_default(self):
            run("echo hi")
            eq_(sys.stdout.getvalue().strip(), "hi")

        @trap
        def when_echo_True_commands_echoed_in_bold(self):
            run("echo hi", echo=True)
            expected = "\033[1;37mecho hi\033[0m\nhi"
            eq_(sys.stdout.getvalue().strip(), expected)

    #
    # Random edge/corner case junk
    #

    def non_stupid_OSErrors_get_captured(self):
        # Somehow trigger an OSError saying "Input/output error" within
        # pexpect.spawn().interact() & assert it is in result.exception
        skip()

    def KeyboardInterrupt_on_stdin_doesnt_flake(self):
        # E.g. inv test => Ctrl-C halfway => shouldn't get buffer API errors
        skip()

    class funky_characters_in_stdout:
        def basic_nonstandard_characters(self):
            # Crummy "doesn't explode with decode errors" test
            if WINDOWS:
                cmd = "type tree.out"
            else:
                cmd = "cat tree.out"
            run(cmd, hide='both')

        def nonprinting_bytes(self):
            # Seriously non-printing characters (i.e. non UTF8) also don't
            # asplode
            run("echo '\xff'", hide='both')

        @skip_if_windows
        def nonprinting_bytes_pty(self):
            # PTY use adds another utf-8 decode spot which can also fail.
            run("echo '\xff'", pty=True, hide='both')


class Local_(Spec):
    def setup(self):
        os.chdir(support)
        self.both = "echo foo && {0} bar".format(error_command)

    def teardown(self):
        reset_cwd()

    @skip_if_windows
    def stdout_contains_both_streams_under_pty(self):
        r = run(self.both, hide='both', pty=True)
        eq_(r.stdout, 'foo\r\nbar\r\n')

    @skip_if_windows
    def stderr_is_empty_under_pty(self):
        r = run(self.both, hide='both', pty=True)
        eq_(r.stderr, '')
