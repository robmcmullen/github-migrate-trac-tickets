#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Migrate trac tickets from DB into GitHub using v3 API.
# Transform milestones to milestones, components to labels.
# The code merges milestones and labels does NOT attempt to prevent
# duplicating tickets so you'll get multiples if you run repeatedly.
# See API docs: http://developer.github.com/v3/issues/

# TODO:
# - it's not getting ticket *changes* from 'comments', like milestone changed.
# - should I be migrating Trac 'keywords' to Issue 'labels'?
# - list Trac users, get GitHub collaborators, define a mapping for issue assignee.

import sys, re, time
import datetime
# TODO: conditionalize and use 'json'
import logging
from optparse import OptionParser
import sqlite3

from github import GitHub

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

class TracTicket(object):
    @classmethod
    def iter_tickets(cls):
        tickets = trac.sql('SELECT id, summary, description , owner, reporter, milestone, component, status, time FROM ticket ORDER BY id') # LIMIT 5
        for values in tickets:
            yield TracTicket(*values)
    
    @classmethod
    def get_dummy_data(cls):
        dummy = {'title': "Filler ticket",
                 'body': "Empty ticket to maintain numbering with Trac",
                 'state': "closed",
                 }
        return dummy
    
    @classmethod
    def add_dummy_ticket(cls):
        dummy = cls.get_dummy_data()
        dummy_issue = github.issues(data=dummy)
        gid = dummy_issue['number']
        github.issues(id_=gid, data={'state': 'closed'})
        logging.info("Added dummy ticket %d to maintain numbering with Trac" % gid)
        return gid
    
    @classmethod
    def replace_with_dummy(cls, gid):
        dummy = cls.get_dummy_data()
        github.issues(id_=gid, data=dummy)
        logging.info("Replaced existing ticket %d with dummy data to maintain numbering with Trac" % gid)
    
    def __init__(self, *values):
        self.tid, self.summary, self.description, self.owner, self.reporter, self.milestone, self.component, self.status, self.time = values
    
    def get_github_summary_data(self):
        issue = {'title': ticket.summary}
        return issue
    
    def get_github_data(self, milestone_map, labels):
        issue = {'title': ticket.summary}
        text = self.get_description()
        if text:
            issue['body'] = text
        text = self.milestone.strip()
        if text:
            m = milestone_map.get(text)
            if m:
                issue['milestone'] = m
        if self.component:
            if self.component not in labels:
                # GitHub creates the 'url' and 'color' fields for us
                github.labels(data={'name': ticket.component})
                labels[ticket.component] = 'CREATED' # keep track of it so we don't re-create it
                logging.debug("adding component as new label=%s" % ticket.component)
            issue['labels'] = [ticket.component]
            
        # We have to create/map Trac users to GitHub usernames before we can assign
        # them to tickets; don't see how to do that conveniently now.
        # if owner.strip():
        #     ticket['assignee'] = owner.strip()
        return issue
    
    def get_description(self):
        r = self.reporter.strip()
        text = wiki.convert_author(r)
        text += wiki.convert_time(self.time)
        text += wiki.convert(self.description.strip())
        return text

class WikiConverter(object):
    # Regexps to convert to markdown borrowed and modified from http://zim-wiki.org/wiki/doku.php?id=script_to_convert_moinmoin_pages_to_zim
    regexp = (
        ('\[(http[^ ]*) ([^\]]*)\]', '[\\2](\\1)'),        # web link
#        ('\[(http.*)\]', '[[\\1]]'),                  # web link
        ('^\s\s\s\s\*', '\t\t\t*'),
        ('^\s\s\s\*', '\t\t*'),
        ('^\s\s\*', '\t*'),
        ('^\s\*', '*'),                           # lists must have 2 whitespaces before the asterisk
        ('^\s\s\s\s[0-9]\.', '    1.'),
        ('^\s\s[0-9]\.', '  1.'),
        ('^\s[0-9]\.', '1.'),
        ('\'{5}([^\']*)\'{5}', '**//\\1//**'),          # bold and italic
        ('\'{3}([^\']*)\'{3}', '**\\1**'),              # bold
        ('\'{2}([^\']*)\'{2}', '//\\1//'),              # italic
        ('^\s*\{{3}\s*$', '```'),                              # open code/verbatim line segment
        ('^\s*\}{3}\s*$', '```'),                              # close code/verbatim line segment
        ('\{{3}', '`'),                              # open code/verbatim line segment
        ('\}{3}', '`'),                              # close code/verbatim line segment
    )
    
    def __init__(self, rev_map):
        self.svn_to_git = rev_map
    
    def convert_line(self, line):
        for item in self.regexp:
            line = re.sub(item[0], item[1], line)
        match = re.search("\(In \[([0-9]+)\]\)(.+)", line)
        if match:
            rev = int(match.group(1))
            git = self.svn_to_git.get(rev, "[old svn rev%d]" % rev)
            line = "%s%s" % (git, match.group(2))
            print "Found rev %d: %s" % (rev, line)
        match = re.search("(.+)r([0-9]+)(.+)", line)
        if match:
            rev = int(match.group(2))
            git = self.svn_to_git.get(rev, "[old svn rev%d]" % rev)
            line = "%s%s%s" % (match.group(1), git, match.group(3))
            print "Found rev %d: %s" % (rev, line)
        return line
    
    def convert(self, text):
        lines = []
        for line in text.splitlines():
            lines.append(self.convert_line(line))
        return "\n".join(lines)
    
    def convert_author(self, r, intro="reported by"):
        # FIXME: modify this list to treat any of the entries as yourself;
        # otherwise the author will be annotated in the ticket.
        
        # Author email addresses are truncated to prevent spam to them
        if r and r not in ["anonymous"]:
            if "@" in r:
                name, domain = r.split("@")
                r = "%s@..." % name
            text = "**[%s %s]** " % (intro, r)
        else:
            text = ""
        return text
    
    def convert_time(self, t):
        try:
            return "*[Trac time %s]* " % time.strftime("%Y%m%d %H%M%SZ", time.gmtime(int(t)))
        except:
            return ""

def svn_git_revision_map(gitdir):
    rmap = {}
    import subprocess as sub
    p = sub.Popen(["git", "log"], cwd=gitdir, stdout=sub.PIPE, stderr=sub.PIPE)
    stdout, stderr = p.communicate()
    for line in stdout.splitlines():
        #print line
        if line.startswith("commit "):
            git = line[7:]
        elif "svn-revision:" in line:
            svn = int(line.split()[1][1:])
            rmap[svn] = git
            git = None
    #print rmap
    return rmap
    
# Warning: optparse is deprecated in python-2.7 in favor of argparse
usage = """
  %prog [options] trac_db_path github_username github_password github_repo

  Convert trac tickets to github issues, maintaining issue numbers by inserting
  dummy filler tickets into the github database if necessary.

  The trac_db_path is a path to a local copy of the sqlite3 database used by
  Trac.  MySQL or other databases aren't supported directly, so you'll have to
  convert them to sqlite first.

  The github_repo combines user or organization and specific repo like
  "myorg/myapp"

  The [-r git_dir] argument will create a mapping from git-svn ID to git SHA1
  so that references to revisions in ticket descriptions can be maintained.
  This requires a little up-front work to the git-svn repository.  At the
  command line, type:

  git filter-branch -f --msg-filter 'sed -e "s/^git-svn-id:.*@\([0-9]*\).*/svn-revision: r\1/" -e "/./,/^$/!d"' HEAD

  This converts the git-svn-id: line to a more succinct line like "svn-
  revision: r1234".

  Then, use this script with the -r option and any trac references using the
  r1234 syntax will be converted to a git SHA1 commit ID.

  Run with:

  ./trac-tickets-to-gh.py -r /path/to/repository trac.db github_username "github_password" github_username/projectname

  If you want to test a small set of tickets, say tickets 1 through 10, use:

  ./trac-tickets-to-gh.py -r /path/to/repository trac.db github_username "github_password" github_username/projectname -s 1 -e 10

  If you want to continue on after stopping at some point, use:

  ./trac-tickets-to-gh.py -r /path/to/repository trac.db github_username "github_password" github_username/projectname -s 11
"""
parser = OptionParser(usage=usage)
parser.add_option('-q', '--quiet', action="store_true", default=False,
                  help='Decrease logging of activity')
parser.add_option('-r', '--revision-map', action="store", default="",
                  help='Get svn to git revision map from git dir')
parser.add_option('-s', '--ticket-start', action="store", default=1, type=int,
                  help='Starting ticket number (default: 1)')
parser.add_option('-e', '--ticket-end', action="store", default=-1, type=int,
                  help='Ending ticket number, inclusive (default: all remaining tickets)')
parser.add_option('-c', '--comments-only', action="store_true", default=False,
                  help='Add comments to tickets; don\'t add any new tickets')

(options, args) = parser.parse_args()
if options.revision_map:
    rmap = svn_git_revision_map(options.revision_map)
else:
    rmap = None

wiki = WikiConverter(rmap)
#print rmap[1]
#text = open("test.wiki").read()
#print wiki.convert(text)

try:
    [trac_db_path, github_username, github_password, github_repo] = args
except ValueError:
    parser.error('Wrong number of arguments')
if not '/' in github_repo:
    parser.error('Repo must be specified like "organization/project"')

if options.quiet:
    logging.basicConfig(level=logging.INFO)
else:
    logging.basicConfig(level=logging.DEBUG)

trac = Trac(trac_db_path)
logging.info("Checking reporters...")

#for ticket in TracTicket.iter_tickets():
#    print ticket.tid, ticket.get_description()
#comments = trac.sql('SELECT author, newvalue AS body FROM ticket_change WHERE field="comment" AND ticket=%s' % 368)
#for author, body in comments:
#    body = body.strip()
#    if body:
#        # prefix comment with author as git doesn't keep them separate
#        text = wiki.convert_author(author, "comment by")
#        text += wiki.convert(body)
#        print text
#sys.exit()

github = GitHub(github_username, github_password, github_repo)

# Show the Trac usernames assigned to tickets as an FYI

logging.info("Getting Trac ticket owners (will NOT be mapped to GitHub username)...")
for (username,) in trac.sql('SELECT DISTINCT owner FROM ticket'):
    if username:
        username = username.strip() # username returned is tuple like: ('phred',)
        logging.debug("Trac ticket owner: %s" % username)


# Get GitHub labels; we'll merge Trac components into them

logging.info("Getting existing GitHub labels...")
labels = {}
for label in github.labels():
    labels[label['name']] = label['url'] # ignoring 'color'
    logging.debug("label name=%s" % label['name'])

# Get any existing GitHub milestones so we can merge Trac into them.
# We need to reference them by numeric ID in tickets.
# API returns only 'open' issues by default, have to ask for closed like:
# curl -u 'USER:PASS' https://api.github.com/repos/USERNAME/REPONAME/milestones?state=closed

logging.info("Getting existing GitHub milestones...")
milestone_id = {}
for m in github.milestones():
    milestone_id[m['title']] = m['number']
    logging.debug("milestone (open)   title=%s" % m['title'])
for m in github.milestones(query='state=closed'):
    milestone_id[m['title']] = m['number']
    logging.debug("milestone (closed) title=%s" % m['title'])

# We have no way to set the milestone closed date in GH.
# The 'due' and 'completed' are long ints representing datetimes.

logging.info("Migrating Trac milestones to GitHub...")
milestones = trac.sql('SELECT name, description, due, completed FROM milestone')
for name, description, due, completed in milestones:
    name = name.strip()
    logging.debug("milestone name=%s due=%s completed=%s" % (name, due, completed))
    if name and name not in milestone_id:
        if completed:
            state = 'closed'
        else:
            state = 'open'
        milestone = {'title': name,
                     'state': state,
                     'description': description,
                     }
        if due:
            milestone['due_on'] = datetime.datetime.fromtimestamp(
                due / 1000 / 1000).isoformat()
        logging.debug("milestone: %s" % milestone)
        gh_milestone = github.milestones(data=milestone)
        milestone_id['name'] = gh_milestone['number']

# Copy Trac tickets to GitHub issues, keyed to milestones above

logging.info("Migrating Trac tickets to GitHub...")
for ticket in TracTicket.iter_tickets():
    if ticket.tid < options.ticket_start:
        continue
    if options.ticket_end > -1 and ticket.tid > options.ticket_end:
        break
    logging.info("Ticket %d: [%s] %s" % (ticket.tid, ticket.owner.strip(), ticket.summary))
    
    if options.comments_only:
        gid = ticket.tid
    else:
        # We can't find out the github ticket number in advance, so attempt to add
        # ticket and see what number results
        issue = ticket.get_github_summary_data()
        gh_issue = github.issues(data=issue)
        gid = gh_issue['number']
        
        # If the ticket number isn't what we expect, handle two cases
        if gid < ticket.tid:
            # If github is behind Trac, just add a bunch of dummy tickets
            TracTicket.replace_with_dummy(gid)
            while gid < ticket.tid - 1:
                gid = TracTicket.add_dummy_ticket()
            
            # Re-add ticket, this time in the correct spot
            issue = ticket.get_github_data(milestone_id, labels)
            gh_issue = github.issues(data=issue)
            gid = gh_issue['number']
        elif gid > ticket.tid:
            logging.error("Github ticket numbering is ahead of track numbering and can't be used.")
            sys.exit(-1)
        else:
            # Update the existing ticket with all the remaining info
            issue = ticket.get_github_data(milestone_id, labels)
            github.issues(id_=gid, data=issue)
        logging.info("Ticket mapping: trac=%d, gh=%d" % (ticket.tid, gid))
    
    # Skip comments while debugging
    if True:
        # Add comments
        comments = trac.sql('SELECT author, time, newvalue FROM ticket_change WHERE field="comment" AND ticket=%s' % ticket.tid)
        for author, time_t, body in comments:
            body = body.strip()
            if body:
                text = wiki.convert_author(author, "comment by")
                text += wiki.convert_time(time_t)
                text += wiki.convert(body)
                if text:
                    # prefix comment with author as git doesn't keep them separate
                    logging.debug('issue comment: %s' % text[:50]) # TODO: escape newlines
                    github.issue_comments(gid, data={'body': text})

    # Close tickets if they need it.
    # The v3 API says we should use PATCH, but
    # http://developer.github.com/v3/ says POST is supported.
    if ticket.status == 'closed':
        github.issues(id_=gid, data={'state': 'closed'})
        logging.debug("close")

trac.close()


