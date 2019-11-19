import sys

from copy import copy
from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import (
    ObjectDoesNotExist,
    ValidationError,
    MultipleObjectsReturned,
)
from django.core.management.color import color_style
from edc_auth.codename_tuples import navbar_tuples, get_rando_tuples
from edc_randomization.utils import (
    get_randomizationlist_model,
    get_randomizationlist_model_name,
)
from warnings import warn

from .get_default_codenames_by_group import get_default_codenames_by_group
from .group_names import PII, PII_VIEW
from .codename_tuples import dashboard_tuples

INVALID_APP_LABEL = "invalid_app_label"

style = color_style()


class PermissionsCodenameError(Exception):
    pass


class PermissionsCreatorError(ValidationError):
    pass


class CodenameDoesNotExist(Exception):
    pass


def get_app_label(a):
    a = a.split(".apps.")[0]
    return a.split(".")[-1]


class GroupPermissionsUpdater:
    def __init__(
        self,
        codenames_by_group=None,
        extra_pii_models=None,
        excluded_app_labels=None,
        apps=None,
        verbose=None,
    ):
        self.apps = apps or django_apps
        self.verbose = verbose
        self.excluded_app_labels = excluded_app_labels
        self.codenames_by_group = codenames_by_group
        self.extra_pii_models = extra_pii_models or []
        self.update_group_permissions()

    def update_group_permissions(self):
        if self.verbose:
            sys.stdout.write(
                style.MIGRATE_HEADING("Updating groups and permissions:\n")
            )

        self.create_or_update_groups()
        self.create_permissions_from_tuples("edc_dashboard.dashboard", dashboard_tuples)
        self.create_permissions_from_tuples("edc_navbar.navbar", navbar_tuples)
        self.create_permissions_from_tuples(
            get_randomizationlist_model_name(), self.rando_tuples
        )
        self.remove_permissions_to_dummy_models()
        self.make_randomizationlist_view_only()

        self.update_codenames_by_group()

        if self.verbose:
            sys.stdout.write(style.MIGRATE_HEADING("Done\n"))
            sys.stdout.flush()

    def update_codenames_by_group(self):
        for group_name, codenames in self.codenames_by_group.items():
            if self.verbose:
                sys.stdout.write(f"  * {group_name.lower()}\n")
            try:
                group = self.group_model_cls.objects.get(name=group_name)
            except ObjectDoesNotExist as e:
                raise ObjectDoesNotExist(f"{e} Got {group_name}")

            group.permissions.clear()
            self.add_permissions_to_group_by_codenames(group, codenames)
            if group.name not in [PII, PII_VIEW]:
                self.remove_pii_permissions_from_group(group)
            self.remove_historical_group_permissions(group)

    @property
    def rando_tuples(self):
        return get_rando_tuples()

    @property
    def group_model_cls(self):
        return self.apps.get_model("auth.group")

    @property
    def content_type_model_cls(self):
        return self.apps.get_model("contenttypes.contenttype")

    @property
    def group_names(self):
        return list(self.codenames_by_group.keys())

    @property
    def permission_model_cls(self):
        return self.apps.get_model("auth.permission")

    @property
    def codenames_by_group(self):
        return self._codenames_by_group

    @codenames_by_group.setter
    def codenames_by_group(self, value=None):
        """
        Sets and updates the codenames_by_group.

        Removes codenames that refer to app_labels that are not
        installed.

        excluded_app_labels: Explicitly list the app_labels to remove
        """

        self._codenames_by_group = value or {}

        self._codenames_by_group.update(**get_default_codenames_by_group())
        if not self.excluded_app_labels:
            app_labels = []
            for codenames in self._codenames_by_group.values():
                for codename in codenames:
                    app_label, _ = codename.split(".")
                    app_labels.append(app_label)

            installed_app_labels = list(
                [get_app_label(a) for a in settings.INSTALLED_APPS]
            )
            self.excluded_app_labels = list(
                set(
                    [
                        app_label
                        for app_label in app_labels
                        if app_label not in installed_app_labels
                    ]
                )
            )
        for app_label in ["auth", "sites", "admin"]:
            if app_label in self.excluded_app_labels:
                raise PermissionsCodenameError(
                    f"app_label '{app_label}' not installed but required."
                )
        if self.excluded_app_labels:
            codenames_by_group_copy = {
                k: v for k, v in self._codenames_by_group.items()
            }
            for group_name, codenames in codenames_by_group_copy.items():
                original_codenames = copy(codenames)
                for codename in original_codenames:
                    for app_label in self.excluded_app_labels:
                        if app_label == codename.split(".")[0]:
                            codenames.remove(codename)
                self._codenames_by_group[group_name] = codenames
        return self._codenames_by_group

    def create_or_update_groups(self):
        """Add/Deletes group model instances to match the
        the list of group names.
        """
        for name in self.group_names:
            try:
                self.group_model_cls.objects.get(name=name)
            except ObjectDoesNotExist:
                self.group_model_cls.objects.create(name=name)
        self.group_model_cls.objects.exclude(name__in=self.group_names).delete()

    def make_randomizationlist_view_only(self):
        app_label, model = get_randomizationlist_model(
            apps=self.apps
        )._meta.label_lower.split(".")
        permissions = self.permission_model_cls.objects.filter(
            content_type__app_label=app_label, content_type__model=model
        ).exclude(codename=f"view_{model}")
        codenames = [f"{app_label}.{o.codename}" for o in permissions]
        codenames.extend(
            [
                "edc_randomization.add_randomizationlist",
                "edc_randomization.change_randomizationlist",
                "edc_randomization.delete_randomizationlist",
            ]
        )
        codenames = list(set(codenames))
        for group in self.group_model_cls.objects.all():
            self.remove_permissions_by_codenames(
                group=group, codenames=codenames,
            )

    def remove_permissions_to_dummy_models(self):
        for group in self.group_model_cls.objects.all():
            self.remove_permissions_by_codenames(
                group=group,
                codenames=[
                    "edc_dashboard.add_dashboard",
                    "edc_dashboard.change_dashboard",
                    "edc_dashboard.delete_dashboard",
                    "edc_dashboard.view_dashboard",
                    "edc_navbar.add_navbar",
                    "edc_navbar.change_navbar",
                    "edc_navbar.delete_navbar",
                    "edc_navbar.view_navbar",
                ],
            )

    def create_permissions_from_tuples(self, model=None, codename_tuples=None):
        """Creates custom permissions on model "model".
        """
        if codename_tuples:
            try:
                model_cls = self.apps.get_model(model)
            except LookupError as e:
                warn(f"{e}. Got {model}")
            else:
                content_type = self.content_type_model_cls.objects.get_for_model(
                    model_cls
                )
                for codename_tpl in codename_tuples:
                    app_label, codename, name = self.get_from_codename_tuple(
                        codename_tpl, model_cls._meta.app_label
                    )
                    try:
                        self.permission_model_cls.objects.get(
                            codename=codename, content_type=content_type
                        )
                    except ObjectDoesNotExist:
                        self.permission_model_cls.objects.create(
                            name=name, codename=codename, content_type=content_type
                        )
                    self.verify_codename_exists(f"{app_label}.{codename}")

    def remove_permissions_by_codenames(self, group=None, codenames=None):
        """Remove the given codenames from the given group.
        """
        permissions = self.get_permissions_qs_from_codenames(codenames)
        for permission in permissions:
            group.permissions.remove(permission)

    def get_permissions_qs_from_codenames(self, codenames):
        """Returns a list of permission model instances for the given
        codenames.
        """
        permissions = []
        for dotted_codename in codenames:
            try:
                app_label, codename = self.get_from_dotted_codename(dotted_codename)
            except PermissionsCodenameError as e:
                warn(str(e))
            else:
                try:
                    permissions.append(
                        self.permission_model_cls.objects.get(
                            codename=codename, content_type__app_label=app_label
                        )
                    )
                except ObjectDoesNotExist as e:
                    raise ObjectDoesNotExist(
                        f"{e}. Got codename={codename},app_label={app_label}"
                    )
        return permissions

    def get_from_dotted_codename(self, codename=None):
        """Returns a tuple of app_label, codename.

        Validates given codename.
        """
        if not codename:
            raise PermissionsCodenameError(f"Invalid codename. May not be None.")
        try:
            app_label, _codename = codename.split(".")
        except ValueError as e:
            raise PermissionsCodenameError(
                f"Invalid dotted codename. {e} Got {codename}."
            )
        else:
            try:
                self.apps.get_app_config(app_label)
            except LookupError:
                raise PermissionsCodenameError(
                    f"Invalid app_label in codename. Expected format "
                    f"'<app_label>.<some_codename>'. Got {codename}."
                )
        return app_label, _codename

    def get_from_codename_tuple(self, codename_tpl, app_label=None):
        try:
            value, name = codename_tpl
        except ValueError as e:
            raise ValueError(f"{e} Got {codename_tpl}")
        _app_label, codename = value.split(".")
        if app_label and _app_label != app_label:
            raise PermissionsCreatorError(
                f"app_label in permission codename does not match. "
                f"Expected {app_label}. Got {_app_label}. "
                f"See {codename_tpl}.",
                code=INVALID_APP_LABEL,
            )
        return _app_label, codename, name

    def verify_codename_exists(self, codename):
        app_label, codename = self.get_from_dotted_codename(codename)
        try:
            permission = self.permission_model_cls.objects.get(
                codename=codename, content_type__app_label=app_label
            )
        except ObjectDoesNotExist as e:
            raise CodenameDoesNotExist(f"{e} Got '{app_label}.{codename}'")
        except MultipleObjectsReturned as e:
            raise CodenameDoesNotExist(f"{e} Got '{app_label}.{codename}'")
        return permission

    def add_permissions_to_group_by_codenames(self, group=None, codenames=None):
        if codenames:
            permissions = self.get_permissions_qs_from_codenames(codenames)
            for permission in permissions:
                group.permissions.add(permission)

    def remove_pii_permissions_from_group(self, group):
        default_pii_models = [
            settings.SUBJECT_CONSENT_MODEL,
            "edc_locator.subjectlocator",
            "edc_registration.registeredsubject",
        ]
        default_pii_models.extend(self.extra_pii_models)
        for model in default_pii_models:
            self.remove_permissions_by_model(group, model)
        for model in default_pii_models:
            self.remove_permissions_by_model(group, model)

    def remove_permissions_by_model(self, group=None, model=None):
        try:
            model_cls = self.apps.get_model(model)
        except LookupError as e:
            warn(f"{e}. Got {model}")
        else:
            content_type = self.content_type_model_cls.objects.get_for_model(model_cls)
            for permission in self.permission_model_cls.objects.filter(
                content_type=content_type
            ):
                group.permissions.remove(permission)

    def remove_historical_group_permissions(self, group=None, model=None):
        """Removes permissions for historical models from this
        group.

        Default removes all except `view`.
        """

        opts = dict(codename__contains="_historical")
        if model:
            opts.update(model=model)
        for permission in group.permissions.filter(**opts).exclude(
            codename__startswith="view_historical"
        ):
            group.permissions.remove(permission)