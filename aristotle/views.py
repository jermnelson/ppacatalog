"""
 mod:`views` Default Views for Aristotle App
"""
__author__ = 'Jeremy Nelson'

import logging
from django.views.generic.simple import direct_to_template
import django.utils.simplejson as json
from fixures import json_loader,rst_loader

def background(request):
    """
    Background view for the Aristotle Library Apps project

    :param request: Web request from client
    :rtype: Generated HTML template
    """
    return direct_to_template(request,
                              'background.html',
                              {'app':None,
                               'history':rst_loader.get('project-history'),
                               'institution':json_loader.get('institution'),                               
                               'navbar_menus':json_loader.get('navbar-menus'),
                               'related_resources':rst_loader.get('related-resources'),
                               'user':None})

def default(request):
    """
    Default view for Aristotle Library Apps project

    :param request: Web request from client
    :rtype: Generated HTML template
    """
    app_listing = []
    
    return direct_to_template(request,
                              'index.html',
                              {'app':None,
                               'institution':json_loader.get('institution'),                               
                               'navbar_menus':json_loader.get('navbar-menus'),
                               'portfolio':app_listing,
                               'vision':rst_loader.get('vision-for-aristotle-library-apps'),
                               'user':None})

def starting(request):
    """
    Getting Started view for the Aristotle Library Apps project

    :param request: Web request from client
    :rtype: Generated HTML template
    """
    return direct_to_template(request,
                              'getting-started.html',
                              {'app':None,
                               'steps':[rst_loader.get('installing')],
                               'institution':json_loader.get('institution'),                               
                               'navbar_menus':json_loader.get('navbar-menus'),
                               'user':None})

