# encoding: utf-8

"""
To make sure ``django-versioning`` works smoothly with the admin interface, you should add ``revisions.middleware.VersionedModelRedirectMiddleware`` to your middlewares in ``settings.py``, e.g.::

    MIDDLEWARE_CLASSES = (
        'django.middleware.common.CommonMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'revisions.middleware.VersionedModelRedirectMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
    )
    
To enable versioning in the admin, subclass from revisions.admin.VersionedAdmin instead of from django.admin.ModelAdmin. By default, this class has ``revisions.admin.AutoRevisionForm`` as its form, but you're not tied to this ModelForm: 

* AutoRevisionForm makes sure to clear any revision-specific fields, like log messages. Since these are tied to each individual revision, revision-specific fields should be empty upon each new edit.
* RevisionForm inherits from AutoRevisionForm, but adds a checkbox to the form that allows users to specify they only want to make a small change, and that we ought to save it in-place rather than creating a new revision.
* If you need neither, feel free to use a regular ModelForm instead.

Specify fields that need to be cleared as a list of attribute names like so::

    class MyModel(VersionedModel):
        class Versioning:
            clear_each_revision = ['log_message', 'codename', ]

When in doubt, don't specify your own form and stick to the default: the AutoRevisionForm.
"""

from django.contrib import admin
from revisions.managers import LatestManager
from django import forms
from django.shortcuts import get_object_or_404
from django.utils.encoding import force_unicode
from django.utils.text import capfirst
from django.template.response import TemplateResponse
from django.utils.translation import ugettext_lazy as _
from django.contrib.admin.util import unquote
from django.http import Http404
from django.core.exceptions import ObjectDoesNotExist
from django.forms.models import BaseModelFormSet

class AutoRevisionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(AutoRevisionForm, self).__init__(*args, **kwargs)
        
        for field in self.instance.Versioning.clear_each_revision:
            self.initial[field] = ''

class RevisionForm(AutoRevisionForm):
    small_change = forms.BooleanField(initial=False, 
        help_text="Fixed a typo, changed a couple of words. (Doesn't create a new revision)",
        required=False)

    def clean(self):
        self.instance.is_small_change = self.cleaned_data.get('small_change', False)
        del self.fields['small_change']
        del self.cleaned_data['small_change']
        return self.cleaned_data

class VersionedAdminMixin(object):
    form = AutoRevisionForm
    
    def save_model(self, request, obj, form, change):
        """
        Given a model instance save it to the database.
        """
        obj.revise()
        

class RevisionsHistoryVersionedAdminMixin(VersionedAdminMixin):       
    change_form_template = 'admin/revisions_change_form.html'
    diff_ignored_fields = ['vid', 'vuser', 'vdatetime','cid']
    
    def revisions_history_view(self, request, object_id, extra_context=None):
        "The 'revisions history' admin view for this model."
        model = self.model
        opts = model._meta
        app_label = opts.app_label

        obj = get_object_or_404(model, pk=unquote(object_id))
        context = {
            'title': _('Change history: %s') % force_unicode(obj),
            'revision_list': obj.get_revisions(),
            'module_name': capfirst(force_unicode(opts.verbose_name_plural)),
            'object': obj,
            'app_label': app_label,
            'opts': opts,
        }
        context.update(extra_context or {})
        return TemplateResponse(request, self.object_history_template or [
            "admin/%s/%s/object_revisions_history.html" % (app_label, opts.object_name.lower()),
            "admin/%s/object_revisions_history.html" % app_label,
            "admin/object_revisions_history.html"
        ], context, current_app=self.admin_site.name)
        
        
    def revisions_diff_view(self, request, object_id, diff_object_id, extra_context=None):
        "The 'revisions history' admin view for this model."
        model = self.model
        opts = model._meta
        app_label = opts.app_label




        
        try:
            obj = model.objects.get(pk=unquote(diff_object_id))
        except ObjectDoesNotExist:
            raise Http404('No %s matches the given query.' % model._meta.object_name)
                
        diff_list = []
        for field in model._meta.fields:
            if field.name in self.diff_ignored_fields:
                continue
            
            
            toText = unicode(getattr(obj, field.name))
            if obj.get_revisions().prev:
                fromText = unicode(getattr(obj.get_revisions().prev, field.name))
                diff_list.append({
                                  'name': field.verbose_name,                       
                                  'diff': obj.get_revisions().prev.show_diff_to(obj, field.name),
                                  'from': fromText,
                                  'to': toText
                                  })
            else:
                diff_list.append({
                                  'name': field.verbose_name,                       
                                  'from': '',
                                  'to': toText,
                                  'diff': '<ins style="background:#e6ffe6;">%s</ins>' % fromText
                                  }) 
        
        
        context = {
            'title': _('Change history: %s') % force_unicode(obj),
            'diff_list': diff_list,
            'module_name': capfirst(force_unicode(opts.verbose_name_plural)),
            'object': obj,
            'app_label': app_label,
            'opts': opts,
        }
        context.update(extra_context or {})
        return TemplateResponse(request, self.object_history_template or [
            "admin/%s/%s/object_diff.html" % (app_label, opts.object_name.lower()),
            "admin/%s/object_diff.html" % app_label,
            "admin/object_diff.html"
        ], context, current_app=self.admin_site.name)
        
    def get_urls(self):
        urls = super(RevisionsHistoryVersionedAdminMixin, self).get_urls()
        from django.conf.urls import patterns, url
        
        info = self.model._meta.app_label, self.model._meta.module_name
        
        my_urls = patterns('',
            url(r'^(.+)/revisions-history/$', self.admin_site.admin_view(self.revisions_history_view), name='%s_%s_history' % info),
            url(r'^(.+)/revisions-history/(.+)/$', self.admin_site.admin_view(self.revisions_diff_view), name='%s_%s_history_diff' % info)
        )
        return my_urls + urls
    
    
class RevisionsHistoryVersionedAdmin(RevisionsHistoryVersionedAdminMixin, admin.ModelAdmin):
    pass



from utilities.admin.reverse_inline import ReverseModelMixin, ReverseInlineModelAdmin, ReverseInlineFormSet

class RevisionsReverseInlineFormSet(BaseModelFormSet):
    model = None
    parent_fk_name = ''
    def __init__(self,
                 data = None,
                 files = None,
                 instance = None,
                 prefix = None,
                 queryset = None,
                 save_as_new = False):
        
        try:
            object = getattr(instance, self.parent_fk_name)
        except ObjectDoesNotExist:
            object = None
           
         
        if object:
            object = object.get_latest_revision()
            qs = self.model._default_manager.filter(pk = object.pk)
        else:
            qs = self.model._default_manager.none()
            self.extra = 1
        
        super(RevisionsReverseInlineFormSet, self).__init__(data, files,
                                                       prefix = prefix,
                                                       queryset = qs)
        for form in self.forms:
            form.empty_permitted = False
            

class RevisionsReverseInlineModelAdmin(ReverseInlineModelAdmin):
    def get_formset(self, request, obj = None, **kwargs):
        kwargs['formset'] = RevisionsReverseInlineFormSet
        return super(RevisionsReverseInlineModelAdmin, self).get_formset(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        obj.revise()
