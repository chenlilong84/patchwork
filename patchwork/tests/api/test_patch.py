# Patchwork - automated patch tracking system
# Copyright (C) 2016 Linaro Corporation
#
# SPDX-License-Identifier: GPL-2.0-or-later

import email.parser
from email.utils import make_msgid
import unittest

from django.conf import settings
from django.urls import reverse

from patchwork.models import Patch
from patchwork.tests.api import utils
from patchwork.tests.utils import create_maintainer
from patchwork.tests.utils import create_patch
from patchwork.tests.utils import create_person
from patchwork.tests.utils import create_project
from patchwork.tests.utils import create_state
from patchwork.tests.utils import create_user

if settings.ENABLE_REST_API:
    from rest_framework import status


@unittest.skipUnless(settings.ENABLE_REST_API, 'requires ENABLE_REST_API')
class TestPatchAPI(utils.APITestCase):
    fixtures = ['default_tags']

    @staticmethod
    def api_url(item=None, version=None):
        kwargs = {}
        if version:
            kwargs['version'] = version

        if item is None:
            return reverse('api-patch-list', kwargs=kwargs)
        kwargs['pk'] = item
        return reverse('api-patch-detail', kwargs=kwargs)

    def assertSerialized(self, patch_obj, patch_json):
        self.assertEqual(patch_obj.id, patch_json['id'])
        self.assertEqual(patch_obj.name, patch_json['name'])
        self.assertEqual(patch_obj.msgid, patch_json['msgid'])
        self.assertEqual(patch_obj.state.slug, patch_json['state'])
        self.assertIn(patch_obj.get_mbox_url(), patch_json['mbox'])
        self.assertIn(patch_obj.get_absolute_url(), patch_json['web_url'])
        self.assertIn('comments', patch_json)

        # nested fields

        self.assertEqual(patch_obj.submitter.id,
                         patch_json['submitter']['id'])
        self.assertEqual(patch_obj.project.id,
                         patch_json['project']['id'])

        if patch_obj.series:
            self.assertEqual(1, len(patch_json['series']))
            self.assertEqual(patch_obj.series.id,
                             patch_json['series'][0]['id'])
        else:
            self.assertEqual([], patch_json['series'])

    def test_list_empty(self):
        """List patches when none are present."""
        resp = self.client.get(self.api_url())
        self.assertEqual(status.HTTP_200_OK, resp.status_code)
        self.assertEqual(0, len(resp.data))

    def _create_patch(self, **kwargs):
        person_obj = create_person(email='test@example.com')
        project_obj = create_project(linkname='myproject')
        state_obj = create_state(name='Under Review')
        patch_obj = create_patch(state=state_obj, project=project_obj,
                                 submitter=person_obj, **kwargs)

        return patch_obj

    def test_list_anonymous(self):
        """List patches as anonymous user."""
        # we specifically set series to None to test code that handles legacy
        # patches created before series existed
        patch = self._create_patch(series=None)

        resp = self.client.get(self.api_url())
        self.assertEqual(status.HTTP_200_OK, resp.status_code)
        self.assertEqual(1, len(resp.data))
        patch_rsp = resp.data[0]
        self.assertSerialized(patch, patch_rsp)
        self.assertNotIn('headers', patch_rsp)
        self.assertNotIn('content', patch_rsp)
        self.assertNotIn('diff', patch_rsp)

    @utils.store_samples('patch-list')
    def test_list_authenticated(self):
        """List patches as an authenticated user."""
        patch = self._create_patch()
        user = create_user()

        self.client.force_authenticate(user=user)
        resp = self.client.get(self.api_url())
        self.assertEqual(status.HTTP_200_OK, resp.status_code)
        self.assertEqual(1, len(resp.data))
        patch_rsp = resp.data[0]
        self.assertSerialized(patch, patch_rsp)

    def test_list_filter_state(self):
        """Filter patches by state."""
        self._create_patch()
        user = create_user()

        state_obj_b = create_state(name='New')
        create_patch(state=state_obj_b)
        state_obj_c = create_state(name='RFC')
        create_patch(state=state_obj_c)

        self.client.force_authenticate(user=user)
        resp = self.client.get(self.api_url(), [('state', 'under-review'),
                                                ('state', 'new')])
        self.assertEqual(2, len(resp.data))

    def test_list_filter_project(self):
        """Filter patches by project."""
        patch = self._create_patch()
        user = create_user()

        self.client.force_authenticate(user=user)

        resp = self.client.get(self.api_url(), {'project': 'myproject'})
        self.assertEqual([patch.id], [x['id'] for x in resp.data])

        resp = self.client.get(self.api_url(), {'project': 'invalidproject'})
        self.assertEqual(0, len(resp.data))

    def test_list_filter_submitter(self):
        """Filter patches by submitter."""
        patch = self._create_patch()
        submitter = patch.submitter
        user = create_user()

        self.client.force_authenticate(user=user)

        # test filtering by submitter, both ID and email
        resp = self.client.get(self.api_url(), {'submitter': submitter.id})
        self.assertEqual([patch.id], [x['id'] for x in resp.data])

        resp = self.client.get(self.api_url(), {
            'submitter': 'test@example.com'})
        self.assertEqual([patch.id], [x['id'] for x in resp.data])

        resp = self.client.get(self.api_url(), {
            'submitter': 'test@example.org'})
        self.assertEqual(0, len(resp.data))

    @utils.store_samples('patch-list-1-0')
    def test_list_version_1_0(self):
        """List patches using API v1.0."""
        create_patch()

        resp = self.client.get(self.api_url(version='1.0'))
        self.assertEqual(status.HTTP_200_OK, resp.status_code)
        self.assertEqual(1, len(resp.data))
        self.assertIn('url', resp.data[0])
        self.assertNotIn('web_url', resp.data[0])

    @utils.store_samples('patch-detail')
    def test_detail(self):
        """Show a specific patch."""
        patch = create_patch(
            content='Reviewed-by: Test User <test@example.com>\n',
            headers='Received: from somewhere\nReceived: from another place'
        )

        resp = self.client.get(self.api_url(patch.id))
        self.assertEqual(status.HTTP_200_OK, resp.status_code)
        self.assertSerialized(patch, resp.data)

        # Make sure we don't regress and all headers with the same key are
        # included in the response
        parsed_headers = email.parser.Parser().parsestr(patch.headers, True)
        for key, value in parsed_headers.items():
            self.assertIn(value, resp.data['headers'][key])

        self.assertEqual(patch.content, resp.data['content'])
        self.assertEqual(patch.diff, resp.data['diff'])
        self.assertEqual(0, len(resp.data['tags']))

    @utils.store_samples('patch-detail-1-0')
    def test_detail_version_1_0(self):
        patch = create_patch()

        resp = self.client.get(self.api_url(item=patch.id, version='1.0'))
        self.assertIn('url', resp.data)
        self.assertNotIn('web_url', resp.data)
        self.assertNotIn('comments', resp.data)

    def test_create(self):
        """Ensure creations are rejected."""
        project = create_project()
        patch = {
            'project': project.id,
            'submitter': create_person().id,
            'msgid': make_msgid(),
            'name': 'test-create-patch',
            'diff': 'patch diff',
        }

        # anonymous user
        resp = self.client.post(self.api_url(), patch)
        self.assertEqual(status.HTTP_405_METHOD_NOT_ALLOWED, resp.status_code)

        # superuser
        user = create_maintainer(project)
        user.is_superuser = True
        user.save()
        self.client.force_authenticate(user=user)
        resp = self.client.post(self.api_url(), patch)
        self.assertEqual(status.HTTP_405_METHOD_NOT_ALLOWED, resp.status_code)

    @utils.store_samples('patch-update-error-forbidden')
    def test_update_anonymous(self):
        """Update patch as anonymous user.

        Ensure updates can be performed by maintainers.
        """
        patch = create_patch()
        state = create_state()

        resp = self.client.patch(self.api_url(patch.id), {'state': state.name})
        self.assertEqual(status.HTTP_403_FORBIDDEN, resp.status_code)

    def test_update_non_maintainer(self):
        """Update patch as non-maintainer.

        Ensure updates can be performed by maintainers.
        """
        patch = create_patch()
        state = create_state()
        user = create_user()

        self.client.force_authenticate(user=user)
        resp = self.client.patch(self.api_url(patch.id), {'state': state.name})
        self.assertEqual(status.HTTP_403_FORBIDDEN, resp.status_code)

    @utils.store_samples('patch-update')
    def test_update_maintainer(self):
        """Update patch as maintainer.

        Ensure updates can be performed by maintainers.
        """
        project = create_project()
        patch = create_patch(project=project)
        state = create_state()
        user = create_maintainer(project)

        self.client.force_authenticate(user=user)
        resp = self.client.patch(self.api_url(patch.id),
                                 {'state': state.name, 'delegate': user.id})
        self.assertEqual(status.HTTP_200_OK, resp.status_code, resp)
        self.assertEqual(Patch.objects.get(id=patch.id).state, state)
        self.assertEqual(Patch.objects.get(id=patch.id).delegate, user)

        # (who can unset fields too)
        # we need to send as JSON due to https://stackoverflow.com/q/30677216/
        resp = self.client.patch(self.api_url(patch.id), {'delegate': None},
                                 format='json')
        self.assertEqual(status.HTTP_200_OK, resp.status_code, resp)
        self.assertIsNone(Patch.objects.get(id=patch.id).delegate)

    @utils.store_samples('patch-update-error-bad-request')
    def test_update_invalid_state(self):
        """Update patch with invalid fields.

        Ensure we handle invalid Patch updates.
        """
        project = create_project()
        state = create_state()
        patch = create_patch(project=project, state=state)
        user = create_maintainer(project)

        self.client.force_authenticate(user=user)
        resp = self.client.patch(self.api_url(patch.id), {'state': 'foobar'})
        self.assertEqual(status.HTTP_400_BAD_REQUEST, resp.status_code)
        self.assertContains(resp, 'Expected one of: %s.' % state.name,
                            status_code=status.HTTP_400_BAD_REQUEST)

    def test_update_invalid_delegate(self):
        """Update patch with invalid fields.

        Ensure we handle invalid Patch updates.
        """
        project = create_project()
        state = create_state()
        patch = create_patch(project=project, state=state)
        user_a = create_maintainer(project)
        user_b = create_user()

        self.client.force_authenticate(user=user_a)
        resp = self.client.patch(self.api_url(patch.id),
                                 {'delegate': user_b.id})
        self.assertEqual(status.HTTP_400_BAD_REQUEST, resp.status_code)
        self.assertContains(resp, "User '%s' is not a maintainer" % user_b,
                            status_code=status.HTTP_400_BAD_REQUEST)

    def test_delete(self):
        """Ensure deletions are always rejected."""
        project = create_project()
        patch = create_patch(project=project)

        # anonymous user
        resp = self.client.delete(self.api_url(patch.id))
        self.assertEqual(status.HTTP_405_METHOD_NOT_ALLOWED, resp.status_code)

        # superuser
        user = create_maintainer(project)
        user.is_superuser = True
        user.save()
        self.client.force_authenticate(user=user)
        resp = self.client.delete(self.api_url(patch.id))
        self.assertEqual(status.HTTP_405_METHOD_NOT_ALLOWED, resp.status_code)
