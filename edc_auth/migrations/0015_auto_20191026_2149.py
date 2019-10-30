# Generated by Django 2.2.6 on 2019-10-26 18:49

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("edc_auth", "0014_auto_20191026_1841")]

    operations = [
        migrations.AlterModelOptions(
            name="role", options={"ordering": ["display_index", "display_name"]}
        ),
        migrations.RemoveIndex(model_name="role", name="edc_auth_ro_id_587a9b_idx"),
        migrations.RemoveField(model_name="role", name="field_name"),
        migrations.RemoveField(model_name="role", name="version"),
        migrations.RenameField(
            model_name="role", old_name="name", new_name="display_name"
        ),
        migrations.AddIndex(
            model_name="role",
            index=models.Index(
                fields=["id", "display_name", "display_index"],
                name="edc_auth_ro_id_cc6bf4_idx",
            ),
        ),
    ]
