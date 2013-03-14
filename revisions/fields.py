# encoding: utf-8

from django.db import models
from django import forms
from django.forms.util import flatatt
from django.utils.safestring import mark_safe

"""
1. Shouldn't trigger a django.core.management.validation error (which is tricky because it 
   doesn't check if the field is actually a regular ForeignKey, it just checks if it has a
   ``rel`` attribute.
2. Shouldn't add a true FK to databases that support it (that is, anything other than MySQL 
   and SQLite), but treat the foreign key as a regular integerfield/charfield that just
   happens to be a reference.
"""
    
class ReversionsModelChoiceField(forms.ModelChoiceField):

    def prepare_value(self, value):
        val = super(ReversionsModelChoiceField, self).prepare_value(value)
        if val and not self.queryset.filter(pk = val):
            val = self.queryset.model.objects.get(pk = val).get_latest_revision().pk
        return val       
        


# a pseudo-foreign key that supports referencing either the bundle or the individual revision
class ReversionsForeignKey(models.ForeignKey):
    
    def formfield(self, **kwargs):
        db = kwargs.pop('using', None)
        if isinstance(self.rel.to, basestring):
            raise ValueError("Cannot create form field for %r yet, because "
                             "its related model %r has not been loaded yet" %
                             (self.name, self.rel.to))
        defaults = {
            'form_class': ReversionsModelChoiceField,
            'queryset': self.rel.to._default_manager.using(db).complex_filter(self.rel.limit_choices_to),
            'to_field_name': self.rel.field_name,
        }
        defaults.update(kwargs)
        return super(ReversionsForeignKey, self).formfield(**defaults)
   
 
   
class ReversionsManyToManyField(models.ManyToManyField):  
    
    def value_from_object(self, obj):
        "Returns the value of this field in the given model instance."       
        print 'tady'
        rev_obj_pks = []
        for rev_obj in getattr(obj, self.attname).model.objects.filter(**{'%s__pk' % obj.__class__.__name__.lower(): obj.pk}):
            rev_obj_pks.append(rev_obj.get_latest_revision().pk)
            
        return getattr(obj, self.attname).model._default_manager.filter(pk__in=rev_obj_pks)
  
    