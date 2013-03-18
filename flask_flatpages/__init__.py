# coding: utf8
"""
    flask_flatpages
    ~~~~~~~~~~~~~~~~~~

    Flask-FlatPages provides a collections of pages to your Flask application.
    Pages are built from “flat” text files as opposed to a relational database.

    :copyright: (c) 2010 by Simon Sapin.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import with_statement

import os.path
import itertools
import datetime

import yaml
import markdown
import werkzeug
import flask

import filters


VERSION = '0.4'


def pygmented_markdown(text):
    """Render Markdown text to HTML. Uses the `Codehilite`_ extension
    if `Pygments`_ is available.

    .. _Codehilite: http://www.freewisdom.org/projects/python-markdown/CodeHilite
    .. _Pygments: http://pygments.org/
    """
    try:
        import pygments
    except ImportError:
        extensions = []
    else:
        extensions = ['codehilite']
    return markdown.markdown(text, extensions)


def pygments_style_defs(style='default'):
    """:return: the CSS definitions for the `Codehilite`_ Markdown plugin.

    :param style: The Pygments `style`_ to use.

    Only available if `Pygments`_ is.

    .. _Codehilite: http://www.freewisdom.org/projects/python-markdown/CodeHilite
    .. _Pygments: http://pygments.org/
    .. _style: http://pygments.org/docs/styles/
    """
    import pygments.formatters
    formater = pygments.formatters.HtmlFormatter(style=style)
    return formater.get_style_defs('.codehilite')


class Page(object):
    def __init__(self, path, meta_yaml, body, html_renderer):
        #: Path this pages was obtained from, as in ``pages.get(path)``.
        self.path = path
        #: Content of the pages.
        self.body = body
        self._meta_yaml = meta_yaml
        self.html_renderer = html_renderer

    def __repr__(self):
        return '<Page %r>' % self.path

    @werkzeug.cached_property
    def html(self):
        """The content of the page, rendered as HTML by the configured renderer.
        """
        return self.html_renderer(self.body)

    def __html__(self):
        """In a template, ``{{ page }}`` is equivalent to
        ``{{ page.html|safe }}``.
        """
        return self.html

    @werkzeug.cached_property
    def meta(self):
        """A dict of metadata parsed as YAML from the header of the file."""
        meta = yaml.safe_load(self._meta_yaml)
        # YAML documents can be any type but we want a dict
        # eg. yaml.safe_load('') -> None
        #     yaml.safe_load('- 1\n- a') -> [1, 'a']
        if not meta:
            return {}
        if not isinstance(meta, dict):
            raise ValueError("Excpected a dict in metadata for '%s', got %s"
                % (self.path, type(meta).__name__))
        return meta

    def __getitem__(self, name):
        """Shortcut for accessing metadata.

        ``page['title']`` or, in a template, ``{{ page.title }}`` are
        equivalent to ``page.meta['title']``.
        """
        return self.meta[name]

    def __getattr__(self, name):
        """Shortcut for accessing metadata with an attribute."""
        return self.meta[name]


class PageSet(list):
    """A page container that allows to filter and order pages."""

    MINDATE = datetime.date(datetime.MINYEAR, 1, 1)

    def order_by(self, key):
        """Returns pages sorted by ``key``.

        This naively works only with dates so far.
        """
        if key[0] == '-':
            rev = True
            key = key[1:]
        else:
            rev = False

        def get_meta(page):
            return page[key] if key in page.meta else self.MINDATE

        return sorted(self, reverse=rev, key=get_meta)

    def filter(self, negate=False, *args, **kwargs):
        """Returns pages matching the specified filters.

        So far it only works on the metadata fields, not the body.

        The syntax follows Django's conventions, where operators are
        indicated using '__' (``meta_field_name``__``operator``=``value``).
        >>> pages.filter(created__isnull=False)

        Unlike Django, however, additional kwargs are joined using
        OR instead of AND.
        This would return pages where the 'created' field exists
        *OR* the title is 'Hello'.
        >>> pages.filter(created__isnull=False, title='Hello')

        If you want to AND, just chain multiple filter()s together.
        >>> pages.filter(created__isnull=False).filter(title='Hello')
        """
        _filters = []
        filtered = PageSet()
        for field, value in kwargs.iteritems():
            try:
                field_name, condition = field.split('__', 1)
            except ValueError:
                field_name = field
                condition = 'exact'
            else:
                # workaround for reserved word
                if condition == 'in':
                    condition = 'in_'
            _filters.append((field_name, condition, value))
        for page in self:
            for filt in _filters:
                field, cond, val = filt
                try:
                    result = getattr(filters, cond)(page, field, val)
                except (AttributeError, TypeError):
                    raise ValueError("Unknown operator '%s'" % cond)
                else:
                    if negate:
                        result = not result
                    if result and page not in filtered:
                        filtered.append(page)
        return filtered


class FlatPages(object):
    """
    A collections of :class:`Page` objects.

    :param app: your application. Can be omited if you call
                :meth:`init_app` later.
    :type app: Flask instance

    """
    def __init__(self, app=None):

        #: dict of filename: (page object, mtime when loaded)
        self._file_cache = {}

        if app:
            self.init_app(app)


    def init_app(self, app):
        """ Used to initialize an application, useful for
        passing an app later and app factory patterns.

        :param app: your application
        :type app: Flask instance

        """

        app.config.setdefault('FLATPAGES_ROOT', 'pages')
        app.config.setdefault('FLATPAGES_EXTENSION', '.html')
        app.config.setdefault('FLATPAGES_ENCODING', 'utf8')
        app.config.setdefault('FLATPAGES_HTML_RENDERER', pygmented_markdown)
        app.config.setdefault('FLATPAGES_AUTO_RELOAD', 'if debug')

        self.app = app

        app.before_request(self._conditional_auto_reset)

    def _conditional_auto_reset(self):
        """Reset if configured to do so on new requests."""
        auto = self.app.config['FLATPAGES_AUTO_RELOAD']
        if auto == 'if debug':
            auto = self.app.debug
        if auto:
            self.reload()

    def reload(self):
        """Forget all pages.

        All pages will be reloaded next time they're accessed

        """
        try:
            # This will "unshadow" the cached_property.
            # The property will be re-executed on next access.
            del self.__dict__['_pages']
        except KeyError:
            pass

    def __iter__(self):
        """Iterate on all :class:`Page` objects."""
        return self._pages.itervalues()

    def get(self, path, default=None):
        """Returns the :class:`Page` object at ``path``, or ``default``
        if there is no such page.

        """
        # This may trigger the property. Do it outside of the try block.
        pages = self._pages
        try:
            return pages[path]
        except KeyError:
            return default

    def get_or_404(self, path):
        """Returns the :class:`Page` object at ``path``,
        or raise :class:`NotFound` if there is no such page.
        This is caught by Flask and triggers a 404 error.

        """
        page = self.get(path)
        if not page:
            flask.abort(404)
        return page

    def order_by(self, key):
        #TODO: Implement caching
        return PageSet(self._pages.itervalues()).order_by(key)

    def filter(self, *args, **kwargs):
        #TODO: Implement caching
        return PageSet(self._pages.itervalues()).filter(*args, **kwargs)

    def exclude(self, *args, **kwargs):
        """A negated filter."""
        return self.filter(negate=True, *args, **kwargs)

    @property
    def root(self):
        """Full path to the directory where pages are looked for.

        It is the `FLATPAGES_ROOT` config value, interpreted as relative to
        the app root directory.

        """
        return os.path.join(self.app.root_path,
                            self.app.config['FLATPAGES_ROOT'])

    @werkzeug.cached_property
    def _pages(self):
        """Walk the page root directory an return a dict of
        unicode path: page object.

        """
        def _walk(directory, path_prefix=()):
            for name in os.listdir(directory):
                full_name = os.path.join(directory, name)
                if os.path.isdir(full_name):
                    _walk(full_name, path_prefix + (name,))
                elif name.endswith(extension):
                    name_without_extension = name[:-len(extension)]
                    path = u'/'.join(path_prefix + (name_without_extension,))
                    pages[path] = self._load_file(path, full_name)

        extension = self.app.config['FLATPAGES_EXTENSION']
        pages = {}
        # Fail if the root is a non-ASCII byte string. Use Unicode.
        _walk(unicode(self.root))
        return pages

    def _load_file(self, path, filename):
        mtime = os.path.getmtime(filename)
        cached = self._file_cache.get(filename)
        if cached and cached[1] == mtime:
            # cached == (page, old_mtime)
            page = cached[0]
        else:
            with open(filename) as fd:
                content = fd.read().decode(
                    self.app.config['FLATPAGES_ENCODING'])
            page = self._parse(content, path)
            self._file_cache[filename] = page, mtime
        return page

    def _parse(self, string, path):
        lines = iter(string.split(u'\n'))
        # Read lines until an empty line is encountered.
        meta = u'\n'.join(itertools.takewhile(unicode.strip, lines))
        # The rest is the content. `lines` is an iterator so it continues
        # where `itertools.takewhile` left it.
        content = u'\n'.join(lines)

        html_renderer = self.app.config['FLATPAGES_HTML_RENDERER']
        if not callable(html_renderer):
            html_renderer = werkzeug.import_string(html_renderer)
        return Page(path, meta, content, html_renderer)
