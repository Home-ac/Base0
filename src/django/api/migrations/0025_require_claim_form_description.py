# Generated by Django 2.0.13 on 2019-07-16 14:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0024_add_affiliations_and_certifications_to_facility_claim'),
    ]

    operations = [
        migrations.AlterField(
            model_name='facilityclaim',
            name='facility_description',
            field=models.TextField(help_text='A description of the facility', verbose_name='description'),
        ),
        migrations.AlterField(
            model_name='historicalfacilityclaim',
            name='facility_description',
            field=models.TextField(help_text='A description of the facility', verbose_name='description'),
        ),
    ]
