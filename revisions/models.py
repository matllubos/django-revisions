# encoding: utf-8

import uuid
import difflib
from datetime import date
from django.db import models
from django.utils.translation import ugettext as _
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import User
from django.utils.translation import ugettext_lazy as _
from revisions import managers, utils
import inspect

# the crux of all errors seems to be that, with VersionedBaseModel, 
# doing setattr(self, self.pk_name, None) does _not_ lead to creating
# a new object, and thus versioning as a whole doesn't work

# the only thing lacking from the VersionedModelBase is a version id.
# You may use VersionedModelBase if you need to specify your own 
# AutoField (e.g. using UUIDs) or if you're trying to adapt an existing
# model to ``django-revisions`` and have an AutoField not named
# ``vid``.

class VersionedModelBase(models.Model, utils.ClonableMixin):
    @classmethod
    def get_base_model(cls):
        base = cls
        while isinstance(base._meta.pk, models.OneToOneField):
            base = base._meta.pk.rel.to
        return base    

    @property
    def base_model(self):
        return self.get_base_model()

    @property
    def pk_name(self):
        return self.base_model._meta.pk.attname

    # For UUIDs in particular, we need a way to know the order of revisions
    # e.g. through a ``changed`` datetime field.
    @classmethod
    def get_comparator_name(cls):
        if hasattr(cls.Versioning, 'comparator'):
            return cls.Versioning.comparator
        else:
            return cls.get_base_model()._meta.pk.attname

    @property
    def comparator_name(self):
        return self.get_comparator_name()

    @property
    def comparator(self):
        return getattr(self, self.comparator_name)

    @classmethod
    def get_implementations(cls):
        models = [contenttype.model_class() for contenttype in ContentType.objects.all()]
        return [model for model in models if isinstance(model, cls)]

    @property
    def _base_model(self):
        base = self
        while isinstance(base._meta.pk, models.OneToOneField):
            base = base._meta.pk.rel.to
        return base    

    @property
    def _base_table(self):
        return self._base_model._meta.db_table

    # content bundle id
    cid = models.CharField(max_length=36, editable=False, null=True, db_index=True, verbose_name=_('ID'))
    
    # managers
    latest = managers.LatestManager()
    objects = models.Manager()

    # all related revisions, plus easy shortcuts to the previous and next revision
    def get_revisions(self):
        qs = self.__class__.objects.filter(cid=self.cid).order_by(self.comparator_name)
        
        try:
            qs.prev = qs.filter(**{self.comparator_name + '__lt': self.comparator}).order_by('-' + self.comparator_name)[0]
        except IndexError:
            qs.prev = None
        try:
            qs.next = qs.filter(**{self.comparator_name + '__gt': self.comparator})[0]
        except IndexError:
            qs.next = None
        
        return qs
    
    def check_if_latest_revision(self):
        return self.comparator >= max([version.comparator for version in self.get_revisions()])
    
    @classmethod
    def fetch(cls, criterion):
        if isinstance(criterion, int) or isinstance(criterion, str):
            return cls.objects.get(pk=criterion)
        elif isinstance(criterion, models.Model):
            return criterion
        elif isinstance(criterion, date):
            pub_date = cls.Versioning.publication_date
            if pub_date:
                return cls.objects.filter(**{pub_date + '__lte': criterion}).order('-' + self.comparator_name)[0]
            else:
                raise ImproperlyConfigured("""Please specify which field counts as the publication
                    date for this model. You can do so inside a Versioning class. Read the docs 
                    for more info.""")
        else:
            raise TypeError("Can only fetch an object using a primary key, a date or a datetime object.")

    def revert_to(self, criterion):
        revert_to_obj = self.__class__.fetch(criterion)
    
        # You can only revert a model instance back to a previous instance.
        # Not any ol' object will do, and we check for that.
        if revert_to_obj.pk not in self.get_revisions().values_list('pk', flat=True):
            raise IndexError("Cannot revert to a primary key that is not part of the content bundle.")
        else:
            return revert_to_obj.revise()
            
    def get_latest_revision(self):
        return self.get_revisions().order_by('-' + self.comparator_name)[0]
    
    def make_current_revision(self):
        if not self.check_if_latest_revision():
            self.save()

    def show_diff_to(self, to, field):
        lFromText = unicode(getattr(self, field) or '')
        lToText = unicode(getattr(to, field) or '')

        
        from diff_match_patch.diff_match_patch import diff_match_patch

        lDiffClass = diff_match_patch()
        lDiffs = lDiffClass.diff_main(lFromText, lToText)
        return lDiffClass.diff_prettyHtml(lDiffs)
        
        
    def _get_unique_checks(self, exclude=[]):
        # for parity with Django's unique_together notation shortcut
        def parse_shortcut(unique_together):
            unique_together = tuple(unique_together)
            if len(unique_together) and isinstance(unique_together[0], basestring):
                unique_together = (unique_together, )    
            return unique_together
        
        # Django actually checks uniqueness for a single field in the very same way it
        # does things for unique_together, something we happily take advantage of
        unique = tuple([(field,) for field in getattr(self.Versioning, 'unique', ())])
        unique_together = \
            unique + \
            parse_shortcut(getattr(self.Versioning, 'unique_together', ())) + \
            parse_shortcut(getattr(self._meta, 'unique_together', ()))
        
        model = self.__class__()
        model._meta.unique_together = unique_together
        return models.Model._get_unique_checks(model, exclude)          

    def _get_attribute_history(self, name):
        if self.__dict__.get(name, False):
            return [(version.__dict__[name], version) for version in self.get_revisions()]
        else:
            raise AttributeError(name)

    def _get_related_objects(self, relatedmanager):
        """ This method extends a regular related-manager by also including objects
        that are related to other versions of the same content, instead of just to
        this one object. """
        
        related_model = relatedmanager.model
        related_model_name = related_model._meta.module_name
        
        # The foreign key field name on related objects often, by convention,
        # coincides with the name of the class it relates to, but not always, 
        # e.g. you could do something like
        #   class Book(models.Model):
        #       thingmabob = models.ForeignKey(Author)
        #
        # There is, afaik, no elegant way to get a RelatedManager to tell us that
        # related objects refer to this class by 'thingmabob', leading to this
        # kind of convoluted deep dive into the internals of the related class.
        #
        # By all means, I'd welcome suggestions for prettier code.
        ref_name = self._meta._name_map[related_model_name][0].field.name
        pks = [story.pk for story in self.get_revisions()]        
        objs = related_model._default_manager.filter(**{ref_name + '__in': pks})
        
        return objs
    
    def __getattr__(self, name):
        # we catch all lookups that start with 'related_'
        if name.startswith('related_'):
            related_name = "_".join(name.split("_")[1:])
            attribute = getattr(self, related_name, False)
            # we piggyback off of an existing relationship,
            # so the attribute has to exist and it has to be a 
            # RelatedManager or ManyRelatedManager
            if attribute:
                # (we check the module instead of using isinstance, since 
                # ManyRelatedManager is created using a factory so doesn't
                # actually exist inside of the module)
                if attribute.__class__.__dict__['__module__'] == 'django.db.models.fields.related':
                    return self._get_related_objects(attribute)

        if name.endswith('_history'):
            attribute = name.replace('_history', '')
            return self._get_attribute_history(attribute)

        raise AttributeError(name)
            
    def prepare_for_writing(self):
        """
        This method allows you to clear out certain fields in the model that are
        specific to each revision, like a log message.
        """
        for field in self.Versioning.clear_each_revision:
            super(VersionedModelBase, self).__setattr__(field, '')

    def validate_bundle(self):
        # uniqueness constraints per bundle can't be checked at the database level, 
        # which means we'll have to do so in the save method
        if getattr(self.Versioning, 'unique_together', None) or getattr(self.Versioning, 'unique', None):
            # replace ValidationError with IntegrityError because this is what users will expect
            try:
                self.validate_unique()
            except ValidationError, error:
                raise IntegrityError(error)

    def revise(self):
        self.validate_bundle()
        if not self.pk:
            return self.save()
        return self.clone()

    def save(self, *vargs, **kwargs):    
        # The first revision of a piece of content won't have a bundle id yet, 
        # and because the object isn't persisted in the database, there's no 
        # primary key either, so we use a UUID as the bundle ID.
        # 
        # (Note for smart alecks: Django chokes on using super/save() more than
        # once in the save method, so doing a preliminary save to get the PK
        # and using that value for a bundle ID is rather hard.)
        if not self.cid:
            self.cid = uuid.uuid4().hex

        self.validate_bundle()
        super(VersionedModelBase, self).save(*vargs, **kwargs)
        
    def delete_revision(self, *vargs, **kwargs):
        super(VersionedModelBase, self).delete(*vargs, **kwargs)
    
    def delete(self, *vargs, **kwargs):
        for revision in self.get_revisions():
            revision.delete_revision(*vargs, **kwargs)

    class Meta:
        abstract = True
    
    class Versioning:
        clear_each_revision = []
        publication_date = None
        unique_together = ()

class VersionedModel(VersionedModelBase):
    vid = models.AutoField(primary_key=True)
    vdatetime = models.DateTimeField(_(u'Last changed'), auto_now=True, editable=False)
    vuser = models.ForeignKey(User, null=True, blank=True, editable=False, related_name="%(app_label)s_%(class)s_vuser")
    
    class Meta:
        abstract = True

class TrashableModel(models.Model):
    """ Users wanting a version history may also expect a trash bin
    that allows them to recover deleted content, as is e.g. the
    case in WordPress. This is that thing. """
    
    _is_trash = models.BooleanField(db_column='is_trash', default=False, editable=False)
    
    @property
    def is_trash(self):
        return self._is_trash
    
    def get_content_bundle(self):
        if isinstance(self, VersionedModelBase):
            return self.get_revisions()
        else:
            return [self]        
    
    def delete(self):
        """
        It makes no sense to trash individual revisions: either you keep a version history or you don't.
        If you want to undo a revision, you should use obj.revert_to(preferred_revision) instead.
        """
        for obj in self.get_content_bundle():
            obj._is_trash = True
            obj.save()
    
    def delete_permanently(self):    
        for obj in self.get_content_bundle():
            super(TrashableModel, obj).delete()
    
    class Meta:
        abstract = True