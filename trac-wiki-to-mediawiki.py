#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Migrate trac wiki from DB to markdown format.  Github doesn't support wiki
# access via the API, so you'll have to update the wiki pages using git on the
# wiki repository.

import os, sys, re, time
import datetime
# TODO: conditionalize and use 'json'
import logging
from optparse import OptionParser
import sqlite3

class Trac(object):
    # We don't have a way to close (potentially nested) cursors

    def __init__(self, trac_db_path):
        self.trac_db_path = trac_db_path
        try:
            self.conn = sqlite3.connect(self.trac_db_path)
        except sqlite3.OperationalError, e:
            raise RuntimeError("Could not open trac db=%s e=%s" % (
                    self.trac_db_path, e))

    def sql(self, sql_query):
        """Create a new connection, send the SQL query, return response.
        We need unique cursors so queries in context of others work.
        """
        cursor = self.conn.cursor()
        cursor.execute(sql_query)
        return cursor

    def close(self):
        self.conn.close()

class TracWiki(object):
    wiki_names = {}
    
    @classmethod
    def iter_wiki(cls):
        entries = trac.sql('SELECT name, text, max(version) FROM wiki GROUP BY name')
        for values in entries:
            wiki = TracWiki(*values)
            cls.wiki_names[wiki.name] = wiki
        for wiki in cls.wiki_names.values():
            yield wiki
    
    def __init__(self, *values):
        self.name = values[0]
        self.text = values[1]
    
    def get_filename(self):
        if self.name == "WikiStart":
            return "Home"
        return self.name
        
    def get_mediawiki(self):
        text = self.text
        
        regexp = (
            ('^\s\s\s\*', '**'),
            ('^\s\s\*', '**'),
            ('^\s\*', '*'),
            ('^\s[0-9]\.', '#'),
            ('\{{3}', '<tt>'),                # open code/verbatim line segment
            ('\}{3}', '</tt>'),               # close code/verbatim line segment
            ('^(.+)::\s*$', ';\\1'),              # definition list
            ('\* (http.*)', '* [\\1]'),                  # web link
            ('\[wiki:(.+?)\]', '[[\\1]]'),
        )
        wiki_words = (
            (r'(!?[A-Z]+[a-z]+[A-Z][A-Za-z]*)', ),  # CamelCase, dont change if CamelCase is in InternalLink
#            ('(\b[A-Z]+[a-z]+[A-Z][A-Za-z]*\b)','[[\\1]]'),  # CamelCase, dont change if CamelCase is in InternalLink
        )
        lines = []
        in_code_block = False
        for line in text.splitlines():
            if re.match(r'\s*\{{3}\s*$', line):
                line = "<pre>"
                in_code_block = True
            elif re.match(r'\s*\}{3}\s*$', line):
                line = "</pre>"
                in_code_block = False
            elif not in_code_block:
                for item in regexp:
                    line = re.sub(item[0], item[1], line)
                for splitter in wiki_words:
                    words = re.split(splitter[0], line)
                    processed = []
                    for word in words:
                        if word in self.wiki_names:
                            word = "[[%s]]" % word
                        elif word.startswith("!"):
                            word = "<nowiki>%s</nowiki>" %word[1:]
                        processed.append(word)
                    line = "".join(processed)
            lines.append(line)
        lines = self.scan_definition_list(lines)
        wiki_text = "\n".join(lines) + "\n"
        wiki_filename = "%s.wiki" % self.get_filename()
        return wiki_filename, wiki_text
    
    def scan_definition_list(self, lines):
        newlines = []
        in_list = False
        for line in lines:
            if line.startswith(";"):
                in_list = True
            elif in_list:
                if line.startswith(" "):
                    line = ":" + line[1:]
                else:
                    in_list = False
            else:
                in_list = False
            newlines.append(line)
        return newlines

    
# Warning: optparse is deprecated in python-2.7 in favor of argparse
usage = """
  %prog [options] trac_db_path git_wiki_path

  The trac_db_path is the path to an sqlite3 database containing your Trac
  data; it might be something like "/tmp/trac_wiki.db"

  The git_wiki_path is the path to a git clone of your wiki pages, the clone
  url to which you can determine from the "Git Access" tab of your wiki home.

  For example, if you have a project 
"""
parser = OptionParser(usage=usage)
parser.add_option('-q', '--quiet', action="store_true", default=False,
                  help='Decrease logging of activity')
parser.add_option('-r', '--revision-map', action="store", default="",
                  help='Get svn to git revision map from git dir')

(options, args) = parser.parse_args()

try:
    [trac_db_path, git_wiki_path] = args
except ValueError:
    parser.error('Wrong number of arguments')

if options.quiet:
    logging.basicConfig(level=logging.INFO)
else:
    logging.basicConfig(level=logging.DEBUG)

trac = Trac(trac_db_path)

logging.info("Migrating Trac wiki entries to mediawiki...")
for wiki in TracWiki.iter_wiki():
    print wiki.name
    filename, wiki_text = wiki.get_mediawiki()
    fh = open(os.path.join(git_wiki_path, filename), "w")
    fh.write(wiki_text.encode("utf-8"))
    fh.close()

trac.close()


