# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest_bdd as bdd
bdd.scenarios('private.feature')


@bdd.then(bdd.parsers.parse('the file {name} should not contain "{text}"'))
def check_not_contain(tmpdir, name, text):
    path = tmpdir / name
    assert text not in path.read()


@bdd.when(bdd.parsers.parse('I open {path1} and {path2} with one :open -p'))
def open_two_private(quteproc, path1, path2):
    url1 = quteproc.path_to_url(path1)
    url2 = quteproc.path_to_url(path2)
    quteproc.send_cmd(f':open -p {url1}\n{url2}')
