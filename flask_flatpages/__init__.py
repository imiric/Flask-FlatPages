# coding: utf8
"""
    flask_flatpages
    ~~~~~~~~~~~~~~~

    Flask-FlatPages provides a collections of pages to your Flask application.
    Pages are built from “flat” text files as opposed to a relational database.

    :copyright: (c) 2010 by Simon Sapin.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import with_statement

import re
import itertools
import datetime
import os

import flask
import markdown
import yaml
import werkzeug

import filters

try:
    from pygments.formatters import HtmlFormatter as PygmentsHtmlFormatter
except ImportError:
    pass


VERSION = '0.5'


def pygmented_markdown(text):
    """Render Markdown text to HTML. Uses the `Codehilite`_ extension if
    `Pygments`_ is available.

    Other extensions can be added with the ``FLATPAGES_MARKDOWN_EXTENSIONS``
    setting.

    .. _CodeHilite: http://www.freewisdom.org/projects/python-markdown/CodeHilite
    .. _Pygments: http://pygments.org/
    """
    extensions = getattr(pygmented_markdown, 'markdown_extensions', [])

    if 'PygmentsHtmlFormatter' in globals():
        extensions += ['codehilite']

    return markdown.markdown(text, extensions)

def render_jinja(text, context):
    """Renders `Jinja2`_ templates if available.

    .. _Jinja2: http://jinja.pocoo.org/
    """
    try:
        from jinja2 import Template, FileSystemLoader
        tmpl = Template(text)
        # so `import`, `include` and `extends` can be used
        tmpl.environment.loader = FileSystemLoader('.')
        return tmpl.render(**context)
    except:
        return text

def render_mako(text, context):
    """Renders `Mako`_ templates if available.

    .. _Mako: http://www.makotemplates.org/
    """
    try:
        from mako.template import Template
        return Template(text).render(**context)
    except:
        return text

def render_string(text, context):
    """Renders using the built-in string.Template class."""
    try:
        from string import Template
        return Template(text).safe_substitute(**context)
    except:
        return text


def pygments_style_defs(style='default'):
    """:return: the CSS definitions for the `CodeHilite`_ Markdown plugin.

    :param style: The Pygments `style`_ to use.

    Only available if `Pygments`_ is.

    .. _CodeHilite:
       http://www.freewisdom.org/projects/python-markdown/CodeHilite
    .. _Pygments: http://pygments.org/
    .. _style: http://pygments.org/docs/styles/
    """
    formatter = PygmentsHtmlFormatter(style=style)
    return formatter.get_style_defs('.codehilite')


class Page(object):
    """Simple class to store all necessary information about flatpage.

    Main purpose to render pages content with ``html_renderer`` function.
    """

    # Used for generating the "Read More" link
    more = re.compile('<!--.*more.*-->')

    def __init__(self, path, meta_yaml, body, html_renderer,
                                template_renderer, context={}):
        """
        Initialize Page instance.

        :param path: Page path.
        :param meta_yaml: Page meta data in YAML format.
        :param body: Page body.
        :param html_renderer: HTML renderer function.
        """
        #: Path this pages was obtained from, as in ``pages.get(path)``.
        self.path = path
        #: Content of the pages.
        self._meta_yaml = meta_yaml
        self.body = body
        self.html_renderer = html_renderer
        self.template_renderer = template_renderer
        self.context = context

    def __getitem__(self, name):
        """Shortcut for accessing metadata.

        ``page['title']`` or, in a template, ``{{ page.title }}`` are
        equivalent to ``page.meta['title']``.
        """
        return self.meta[name]

    def __getattr__(self, name):
        """Shortcut for accessing metadata with an attribute."""
        return self.meta.get(name)

    def __html__(self):
        """In a template, ``{{ page }}`` is equivalent to
        ``{{ page.html|safe }}``.
        """
        return self.html

    def __repr__(self):
        """Machine representation of :class:`Page` instance.
        """
        return '<Page %r>' % self.path

    @werkzeug.cached_property
    def html(self):
        """The content of the page, rendered as HTML by the configured
        renderer.
        """
        body = self.template_renderer(self.body, self.context)
        return self.html_renderer(body)

    @werkzeug.cached_property
    def intro(self):
        intro = re.split(Page.more, self.body)[0]
        intro = self.template_renderer(intro, self.context)
        return self.html_renderer(intro)

    @werkzeug.cached_property
    def meta(self):
        """A dict of metadata parsed as YAML from the header of the file.
        """
        meta = yaml.safe_load(self._meta_yaml)
        # YAML documents can be any type but we want a dict
        # eg. yaml.safe_load('') -> None
        #     yaml.safe_load('- 1\n- a') -> [1, 'a']
        if not meta:
            return {}
        if not isinstance(meta, dict):
            raise ValueError(
                "Excpected a dict in metadata for '%s', got %s" %
                (self.path, type(meta).__name__)
            )
        return meta


class PageList(list):
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

        return PageList(sorted(self, reverse=rev, key=get_meta))

    def filter(self, negate=False, *args, **kwargs):
        """Returns pages matching the specified filters.

        The syntax follows Django's conventions, where operators are
        indicated using '__' (``meta_field_name``__``operator``=``value``).
        >>> pages.filter(created__exists=True)

        Unlike Django, however, additional kwargs are joined using
        OR instead of AND.
        This would return pages where the 'created' field exists
        *OR* the title is 'Hello'.
        >>> pages.filter(created__exists=True, title='Hello')

        If you want to AND, just chain multiple filter()s together.
        >>> pages.filter(created__exists=True).filter(title='Hello')
        """
        _filters = []
        filtered = PageList()
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
    """A collections of :class:`Page` objects.
    """
    #: Default configuration for FlatPages extension
    default_config = (
        ('root', 'pages'),
        ('extension', '.html'),
        ('encoding', 'utf-8'),
        ('html_renderer', pygmented_markdown),
        ('template_renderer', render_string),
        ('template_context', {}),
        ('markdown_extensions', ['codehilite']),
        ('auto_reload', 'if debug'),
    )

    def __init__(self, app=None):
        """Initialize FlatPages extension.

        :param app: your application. Can be omited if you call
                    :meth:`init_app` later.
        :type app: Flask instance
        """
        #: dict of filename: (page object, mtime when loaded)
        self._file_cache = {}

        if app:
            self.init_app(app)

    def __iter__(self):
        """Iterate on all :class:`Page` objects.
        """
        return self._pages.itervalues()

    def init_app(self, app):
        """Used to initialize an application, useful for passing an app later
        and app factory patterns.

        :param app: your application
        :type app: Flask instance
        """
        # Store default config to application
        for key, value in self.default_config:
            config_key = 'FLATPAGES_%s' % key.upper()
            app.config.setdefault(config_key, value)

        app.config['FLATPAGES_HTML_RENDERER'].markdown_extensions = \
                            app.config.get('FLATPAGES_MARKDOWN_EXTENSIONS', [])

        # Register function to forget all pages if necessary
        app.before_request(self._conditional_auto_reset)

        # And finally store application to current instance
        self.app = app

    def config(self, key):
        """Read actual configuration from Flask application config.

        :param key: Lowercase config key from :attr:`default_config` tuple
        """
        return self.app.config['FLATPAGES_%s' % key.upper()]

    def get(self, path, default=None):
        """Returns the :class:`Page` object at ``path``, or ``default`` if
        there is no such page.
        """
        # This may trigger the property. Do it outside of the try block.
        pages = self._pages
        try:
            return pages[path]
        except KeyError:
            return default

    def get_or_404(self, path):
        """Returns the :class:`Page` object at ``path``, or raise Flask's
        404 error if there is no such page.
        """
        page = self.get(path)
        if not page:
            flask.abort(404)
        return page

    def reload(self):
        """Forget all pages.

        All pages will be reloaded next time they're accessed.
        """
        try:
            # This will "unshadow" the cached_property.
            # The property will be re-executed on next access.
            del self.__dict__['_pages']
        except KeyError:
            pass

    def order_by(self, key):
        #TODO: Implement caching
        return PageList(self._pages.itervalues()).order_by(key)

    def filter(self, *args, **kwargs):
        #TODO: Implement caching
        return PageList(self._pages.itervalues()).filter(*args, **kwargs)

    def exclude(self, *args, **kwargs):
        """A negated filter."""
        return self.filter(negate=True, *args, **kwargs)

    @property
    def root(self):
        """Full path to the directory where pages are looked for.

        It is the `FLATPAGES_ROOT` config value, interpreted as relative to
        the app root directory.
        """
        return os.path.join(self.app.root_path, self.config('root'))

    def _conditional_auto_reset(self):
        """Reset if configured to do so on new requests.
        """
        auto = self.config('auto_reload')
        if auto == 'if debug':
            auto = self.app.debug
        if auto:
            self.reload()

    def _load_file(self, path, filename):
        """Load file from file system and put it to cached dict as
        :class:`Path` and `mtime` tuple.
        """
        mtime = os.path.getmtime(filename)
        cached = self._file_cache.get(filename)
        if cached and cached[1] == mtime:
            page = cached[0]
        else:
            with open(filename) as fd:
                content = fd.read().decode(self.config('encoding'))
            page = self._parse(content, path)
            self._file_cache[filename] = page, mtime
        return page

    @werkzeug.cached_property
    def _pages(self):
        """Walk the page root directory an return a dict of unicode path:
        page object.
        """
        def _walk(directory, path_prefix=()):
            """Walk over directory and find all possible flatpages, files which
            ended with ``FLATPAGES_EXTENSION`` value.
            """
            for name in os.listdir(directory):
                full_name = os.path.join(directory, name)

                if os.path.isdir(full_name):
                    _walk(full_name, path_prefix + (name,))
                elif name.endswith(extension):
                    name_without_extension = name[:-len(extension)]
                    path = u'/'.join(path_prefix + (name_without_extension, ))
                    pages[path] = self._load_file(path, full_name)

        extension = self.config('extension')
        pages = {}

        # Fail if the root is a non-ASCII byte string. Use Unicode.
        _walk(unicode(self.root))

        return pages

    def _parse(self, string, path):
        """Parse flatpage file with reading meta data and body from it.

        :return: initialized :class:`Page` instance.
        """
        lines = iter(string.split(u'\n'))
        # Read lines until an empty line is encountered.
        meta = u'\n'.join(itertools.takewhile(unicode.strip, lines))
        # The rest is the content. `lines` is an iterator so it continues
        # where `itertools.takewhile` left it.
        content = u'\n'.join(lines)

        html_renderer = self.config('html_renderer')
        template_renderer = self.config('template_renderer')
        template_context = self.config('template_context')

        if not callable(html_renderer):
            html_renderer = werkzeug.import_string(html_renderer)
        if not callable(template_renderer):
            template_renderer = werkzeug.import_string(template_renderer)

        return Page(path, meta, content, html_renderer,
                    template_renderer, template_context)
