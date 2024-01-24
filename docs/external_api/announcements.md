# Announcements

The following endpoints are available to manage announcements.
Moonraker announcements are effectively push notifications that
can be used to notify users of important information related the
development and status of software in the Klipper ecosystem.
See [the appendix](#appendix) for details on how announcements
work and recommendations for your implementation.

## List announcements

Retrieves a list of current announcements.

```{.http .apirequest title="HTTP Request"}
GET /server/announcements/list?include_dismissed=false
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.announcements.list",
    "params": {
        "include_dismissed": false
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name                | Type | Default | Description                         |
| ------------------- | :--: | ------- | ----------------------------------- |
| `include_dismissed` | bool | true    | When set to false dismissed entries |
|                     |      |         | will be excluded from the returned  |^
|                     |      |         | list of current announcements.      |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "entries": [
        {
            "entry_id": "arksine/moonlight/issue/3",
            "url": "https://github.com/Arksine/moonlight/issues/3",
            "title": "Test announcement 3",
            "description": "Test Description [with a link](https://moonraker.readthedocs.io).",
            "priority": "normal",
            "date": 1647459219,
            "dismissed": false,
            "date_dismissed": null,
            "dismiss_wake": null,
            "source": "moonlight",
            "feed": "moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/2",
            "url": "https://github.com/Arksine/moonlight/issues/2",
            "title": "Announcement Test Two",
            "description": "This is a high priority announcement. This line is included in the description.",
            "priority": "high",
            "date": 1646855579,
            "dismissed": false,
            "date_dismissed": null,
            "dismiss_wake": null,
            "source": "moonlight",
            "feed": "moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/1",
            "url": "https://github.com/Arksine/moonlight/issues/1",
            "title": "Announcement Test One",
            "description": "This is the description.  Anything here should appear in the announcement, up to 512 characters.",
            "priority": "normal",
            "date": 1646854678,
            "dismissed": false,
            "date_dismissed": null,
            "dismiss_wake": null,
            "source": "moonlight",
            "feed": "moonlight"
        },
        {
            "entry_id": "arksine/moonraker/issue/349",
            "url": "https://github.com/Arksine/moonraker/issues/349",
            "title": "PolicyKit warnings; unable to manage services, restart system, or update packages",
            "description": "This announcement is an effort to get ahead of a coming change that will certainly result in issues.  PR #346  has been merged, and with it are some changes to Moonraker's default behavior.",
            "priority": "normal",
            "date": 1643392406,
            "dismissed": false,
            "dismiss_wake": null,
            "source": "moonlight",
            "feed": "Moonraker"
        }
    ],
    "feeds": [
        "moonraker",
        "klipper",
        "moonlight"
    ]
}
```
///

/// api-response-spec
    open: True

| Field     |   Type   | Description                                                |
| --------- | :------: | ---------------------------------------------------------- |
| `entries` | [object] | An array of [announcement entry](#announcement-entry-spec) |
|           |          | objects. The array is sorted by date in descending order   |^
|           |          | (newest to oldest).                                        |^
| `feeds`   | [string] | An array of RSS announcement feeds Moonraker is            |
|           |          | currently subscribed to.                                   |^
{ #list-announcements-spec }

| Field            |     Type      | Description                                                  |
| ---------------- | :-----------: | ------------------------------------------------------------ |
| `entry_id`       |    string     | A unique identifier for the announcement entry.              |
| `url`            |    string     | A url associated with the announcement.  This will link to   |
|                  |               | a GitHub issue for announcements sourced from `moonlight`.   |^
| `title`          |    string     | The title of the announcement.                               |
| `description`    |    string     | A brief description of the announcement. For announcement's  |
|                  |               | sourced from `moonlight` this will be the first paragraph    |^
|                  |               | of the associated GitHub issue.  Moonlight will truncate     |^
|                  |               | truncate descriptions over 512 characters.                   |^
| `priority`       |    string     | The [priority](#announcement-priority-desc) of the           |
|                  |               | announcement.                                                |^
| `date`           | int \| float  | The announcement creation date in unix time.                 |
| `dismissed`      |     bool      | Set to `true` if the announcement has been dismissed.        |
| `date_dismissed` | float \| null | The date, in unix time, the announcement was last dismissed. |
|                  |               | Will be `null` if the announcement has not been dismissed.   |^
| `dismiss_wake`   | float \| null | The amount of time remaining, in seconds, before the entry's |
|                  |               | `dismissed` flag reverts to `true`.  Will be `null` if the   |^
|                  |               | announcement has not been dismissed or if the announcement   |^
|                  |               | was dismissed indefinitely.                                  |^
| `source`         |    string     | The [source](#announcement-source-desc) of the announcement. |
| `feed`           |    string     | The registered RSS feed the announcement belongs to. For     |
|                  |               | announcements sourced internally this will typically be      |^
|                  |               | the name of the component that generated the announcement.   |^
{ #announcement-entry-spec } Announcement Entry

| Priority | Description                                                       |
| -------- | ----------------------------------------------------------------- |
| `normal` | Standard priority. Front-end devs should use their own discretion |
|          | on how to present announcements with normal priority to users.    |^
| `high`   | High priority. It is recommended that front-ends alert the user   |
|          | when a high priority announcement is received.                    |^
{ #announcement-priority-desc } Announcement Priority

| Source      | Description                                                          |
| ----------- | -------------------------------------------------------------------- |
| `moonlight` | The announcement was received from the                               |
|             | [moonlight](https://github.com/Arksine/moonlight) GitHub repo.       |^
|             | Announcements received from local XML files when the `announcements` |^
|             | module is configured in `dev_mode` will also report the source as    |^
|             | `moonlight`.                                                         |^
| `internal`  | The announcement was generated by Moonraker itself.  This could      |
|             | be a component, such as `simplyprint`.                               |^
{ #announcement-source-desc } Announcement Source

///

## Update announcements
Requests that Moonraker check for announcement updates.  This is generally
not required in production, as Moonraker will automatically check for
updates every 30 minutes.  However, during development this endpoint is
useful to force an update when it is necessary to perform integration
tests.

```{.http .apirequest title="HTTP Request"}
POST /server/announcements/update
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.announcements.update",
    "id": 4654
}
```

Returns:

The current list of announcements, in descending order (newest to oldest)
sorted by `date`, and a `modified` field that contains a boolean value
indicating if the update resulted in a change:

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "entries": [
        {
            "entry_id": "arksine/moonraker/issue/349",
            "url": "https://github.com/Arksine/moonraker/issues/349",
            "title": "PolicyKit warnings; unable to manage services, restart system, or update packages",
            "description": "This announcement is an effort to get ahead of a coming change that will certainly result in issues.  PR #346  has been merged, and with it are some changes to Moonraker's default behavior.",
            "priority": "normal",
            "date": 1643392406,
            "dismissed": false,
            "source": "moonlight",
            "feed": "Moonraker"
        },
        {
            "entry_id": "arksine/moonlight/issue/1",
            "url": "https://github.com/Arksine/moonlight/issues/1",
            "title": "Announcement Test One",
            "description": "This is the description.  Anything here should appear in the announcement, up to 512 characters.",
            "priority": "normal",
            "date": 1646854678,
            "dismissed": true,
            "source": "moonlight",
            "feed": "Moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/2",
            "url": "https://github.com/Arksine/moonlight/issues/2",
            "title": "Announcement Test Two",
            "description": "This is a high priority announcement. This line is included in the description.",
            "priority": "high",
            "date": 1646855579,
            "dismissed": false,
            "source": "moonlight",
            "feed": "Moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/3",
            "url": "https://github.com/Arksine/moonlight/issues/3",
            "title": "Test announcement 3",
            "description": "Test Description [with a link](https://moonraker.readthedocs.io).",
            "priority": "normal",
            "date": 1647459219,
            "dismissed": false,
            "source": "moonlight",
            "feed": "Moonlight"
        }
    ],
    "modified": false
}
```
///

/// api-response-spec
    open: True

| Field      |   Type   | Description                                                |
| ---------- | :------: | ---------------------------------------------------------- |
| `entries`  | [object] | An array of [Announcement Entry](#announcement-entry-spec) |
|            |          | objects.                                                   |^
| `modified` |   bool   | A value of `true` indicates that announcement entries      |
|            |          | were changed after the update operation.                   |^

///

## Dismiss an announcement
Sets the dismiss flag of an announcement to `true`.



```{.http .apirequest title="HTTP Request"}
POST /server/announcements/dismiss
Content-Type: application/json

{
    "entry_id": "arksine/moonlight/issue/1",
    "wake_time": 600
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.announcements.dismiss",
    "params": {
        "entry_id": "arksine/moonlight/issue/1",
        "wake_time": 600
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name        |     Type      | Default      | Description                                  |
| ----------- | :-----------: | ------------ | -------------------------------------------- |
| `entry_id`  |    string     | **REQUIRED** | The entry ID of the announcement to dismiss. |
| `wake_time` | float \| null | null         | A time, in seconds, after which the entry's  |
|             |               |              | `dismiss` flag will revert to `true`.  When  |^
|             |               |              | set to `null` the flag will remain `false`   |^
|             |               |              | indefinitely.                                |^

//// tip
The `entry_id` typically contains forward slashes. Remember to escape this value
if including it in the query string of an HTTP request.
////

///

```{.json .apiresponse title="Example Response"}
{
    "entry_id": "arksine/moonlight/issue/1"
}
```

/// api-response-spec
    open: True

| Field      |  Type  | Description                                       |
| ---------- | :----: | ------------------------------------------------- |
| `entry_id` | string | The entry ID of the dismissed announcement entry. |

///

## List announcement feeds

```{.http .apirequest title="HTTP Request"}
GET /server/announcements/feeds
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.announcements.feeds",
    "id": 4654
}
```

```{.json .apiresponse title="Example Response"}
{
    "feeds": [
        "moonraker",
        "klipper"
    ]
}
```

/// api-response-spec
    open: True

| Field   |   Type   | Description                                           |
| ------- | :------: | ----------------------------------------------------- |
| `feeds` | [string] | An array of announcement feeds Moonraker is currently |
|         |          | subscribed to.                                        |^

///

## Subscribe to an announcement feed

Subscribes Moonraker to the announcement feed specified in the request.

```{.http .apirequest title="HTTP Request"}
POST /server/announcements/feed
Content-Type: application/json

{
    "name": "my_feed"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.announcements.post_feed",
    "params": {
        "name": "my_feed"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default      | Description                                        |
| ------ | :----: | ------------ | -------------------------------------------------- |
| `name` | string | **REQUIRED** | The name of the announcement feed to subscribe to. |

///

```{.json .apiresponse title="Example Response"}
{
    "feed": "my_feed",
    "action": "added"
}
```

/// api-response-spec
    open: True

| Field    |  Type  | Description                                              |
| -------- | :----: | -------------------------------------------------------- |
| `feed`   | string | The name of the announcement feed subscribed to.         |
| `action` | string | The [subscription action](#feed-subscribed-action-desc ) |
|          |        | taken by Moonraker after the request has been processed. |^

| Action    | Description                                             |
| --------- | ------------------------------------------------------- |
| `added`   | The requested announcement feed has been subscribed to. |
| `skipped` | Moonraker was already subscribed to the requested feed. |
{ #feed-subscribed-action-desc } Subscription Action

///

## Remove an announcement feed

Removes a subscribed feed.  Only feeds previously subscribed to using
the [subscribe feed](#subscribe-to-an-announcement-feed) endpoint may be
removed. Feeds configured in `moonraker.conf` may not be removed.

```{.http .apirequest title="HTTP Request"}
DELETE /server/announcements/feed?name=my_feed
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.announcements.delete_feed",
    "params": {
        "name": "my_feed"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default      | Description                                  |
| ------ | :----: | ------------ | -------------------------------------------- |
| `name` | string | **REQUIRED** | The name of the announcement feed to remove. |

///


Parameters:

- `name`:  The name of the new feed to remove.  This parameter is required.

Returns:

The name of the new feed and the action taken.  The `action` will be
`removed` if the operation was successful.

```{.json .apiresponse title="Example Response"}
{
    "feed": "my_feed",
    "action": "removed"
}
```

/// api-response-spec
    open: True

| Field    |  Type  | Description                                            |
| -------- | :----: | ------------------------------------------------------ |
| `feed`   | string | The name of the announcement feed removed.             |
| `action` | string | The action taken after the request.  Will be `removed` |
|          |        | upon successful removal.                               |^

//// tip
Unlike the [feed subscription request](#subscribe-to-an-announcement-feed) an
error will be returned if either the feed does not exist or the feed is
configured in `moonraker.conf`.
////
///

## Appendix

This section will provide an overview of how the announcement system
in Moonraker works, how to set up a dev environment, and provide
recommendations on front-end implementation.

### How announcements work

Moonraker announcements are GitHub issues tagged with the `announcement`
label.  GitHub repos may registered with
[moonlight](https://github.com/arksine/moonlight), which is responsible
for generating RSS feeds from GitHub issues using GitHub's REST API. These
RSS feeds are hosted on GitHub Pages, for example Moonraker's feed may be found
[here](https://arksine.github.io/moonlight/assets/moonraker.xml). By
centralizing GitHub API queries in `moonlight` we are able to poll multiple
repos without running into API rate limit issues. Moonlight has has a workflow
that checks all registered repos for new announcements every 30 minutes.  In
theory it would be able to check for announcements in up to 500 repos before
exceeding GitHub's API rate limit.

Moonraker's `[announcements]` component will always check the `klipper` and
`moonraker` RSS feeds.  It is possible to configure additional RSS feeds by
adding them to the `subscriptions` option.  The component will poll configured
feeds every 30 minutes, resulting in maximum of 1 hour for new announcements
to reach all users.

When new issues are tagged with `announcement` these entries will be parsed
and added to the RSS feeds.  When the issue is closed they will be removed from
the corresponding feed.  Moonlight will fetch up to 20 announcements for each
feed, if a repo goes over this limit older announcements will be removed.

/// Note
It is also possible for Moonraker to generate announcements itself.  For
example, if a Moonraker component needs user feedback it may generate an
announcement and notify all connected clients.   From a front-end's
perspective there is no need to treat these announcements differently than
any other announcement.
///

### Setting up the dev environment

Moonraker provides configuration to parse announcements from a local folder
so that it is possible to manually add and remove entries, allowing front-end
developers to perform integration tests:

```ini
# moonraker.conf

[announcements]
dev_mode: True
```

With `dev_mode` enabled, Moonraker will look for`moonraker.xml` and
`klipper.xml` in the following folder:
```shell
~/moonraker/.devel/announcement_xml
```

If moonraker is not installed in the home folder then substitute `~`
for the parent folder location.  This folder is in a hardcoded location
to so as not to expose users to vulnerabilities associated with parsing XML.

It is possible to configure Moonraker to search for your own feeds:

```ini
# moonraker.conf

[announcements]
subscription:
  my_project
dev_mode: True
```

The above configuration would look for `my_project.xml` in addition to
`klipper.xml` and `moonraker.xml`.  The developer may manually create
the xml feeds or they may clone `moonlight` and leverage its script
to generate a feed from issues created on their test repo.  When local
feeds have been modified one may call the [update announcements API](#update-announcements)
to have Moonraker fetch the updates and add/remove entries.

### RSS file structure

Moonlight generates RSS feeds in XML format.  Below is an example generated
from moonlight's own issue tracker:

```xml
<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0" xmlns:moonlight="https://arksine.github.io/moonlight">
    <channel>
        <title>arksine/moonlight</title>
        <link>https://github.com/Arksine/moonlight</link>
        <description>RSS Announcements for Moonraker</description>
        <pubDate>Tue, 22 Mar 2022 23:19:04 GMT</pubDate>
        <moonlight:configHash>f2912192bf0d09cf18d8b8af22b2d3501627043e5afa3ebff0e45e4794937901</moonlight:configHash>
        <item>
            <title>Test announcement 3</title>
            <link>https://github.com/Arksine/moonlight/issues/3</link>
            <description>Test Description [with a link](https://moonraker.readthedocs.io).</description>
            <pubDate>Wed, 16 Mar 2022 19:33:39 GMT</pubDate>
            <category>normal</category>
            <guid>arksine/moonlight/issue/3</guid>
        </item>
        <item>
            <title>Announcement Test Two</title>
            <link>https://github.com/Arksine/moonlight/issues/2</link>
            <description>This is a high priority announcement. This line is included in the description.</description>
            <pubDate>Wed, 09 Mar 2022 19:52:59 GMT</pubDate>
            <category>high</category>
            <guid>arksine/moonlight/issue/2</guid>
        </item>
        <item>
            <title>Announcement Test One</title>
            <link>https://github.com/Arksine/moonlight/issues/1</link>
            <description>This is the description.  Anything here should appear in the announcement, up to 512 characters.</description>
            <pubDate>Wed, 09 Mar 2022 19:37:58 GMT</pubDate>
            <category>normal</category>
            <guid>arksine/moonlight/issue/1</guid>
        </item>
    </channel>
</rss>
```

Each xml file may contain only one `<rss>` element, and each `<rss>` element
may contain only one channel.  All items must be present aside from
`moonlight:configHash`, which is used by the workflow to detect changes to
moonlight's configuration.  Most elements are self explanatory, developers will
be most interested in adding and removing `<item>` elements, as these are
the basis for entries in Moonraker's announcement database.

### Generating announcements from your own repo

As mentioned previously, its possible to clone moonlight and use its rss
script to generate announcements from issues in your repo:

```shell
cd ~
git clone https://github.com/arksine/moonlight
cd moonlight
virtualenv -p /usr/bin/python3 .venv
source .venv/bin/activate
pip install httpx[http2]
deactivate
```

To add your repo edit `~/moonlight/src/config.json`:
```json
{
    "moonraker": {
        "repo_owner": "Arksine",
        "repo_name": "moonraker",
        "description": "API Host For Klipper",
        "authorized_creators": ["Arksine"]
    },
    "klipper": {
        "repo_owner": "Klipper3d",
        "repo_name": "klipper",
        "description": "A 3D Printer Firmware",
        "authorized_creators": ["KevinOConnor"]
    },
    // Add your test repo info here.  It should contain
    // fields matching those in "moonraker" and "klipper"
    // shown above.
}
```

Once your repo is added, create one or more issues on your GitHub
repo tagged with the `announcement` label.  Add the `critical` label to
one if you wish to test high priority announcements.  You may need to
create these labels in your repo before they can be added.

Now we can use moonlight to generate the xml files:
```shell
cd ~/moonlight
source .venv/bin/activate
src/update_rss.py
deactivate
```

After the script has run it will generate the configured RSS feeds
and store them in `~/moonlight/assets`.  If using this method it may
be useful to create a symbolic link to it in Moonraker's devel folder:

```shell
cd ~/moonraker
mkdir .devel
cd .devel
ln -s ~/moonlight/assets announcement_xml
```

If you haven't done so, configure Moonraker to subscribe to your feed
and restart the Moonraker service.  Otherwise you may call the
[announcement update](#update-announcements) API to have Moonraker
parse the announcements from your test feed.


### Implementation details and recommendations

When a front-end first connects to Moonraker it is recommended that the
[list announcements](#list-announcements) API is called to retrieve
the current list of [announcement entries](#announcement-entry-spec).
If the front-end is connected via websocket it may watch for the
[announcement update](./jsonrpc_notifications.md#announcement-update-event) and
[announcement dismissed](./jsonrpc_notifications.md#announcement-dismissed-event)
JSON-RPC notifications and update its UI accordingly.

Front-end devs should decide how they want to present announcements to users.
They could be treated as any other notification, for example a front-end
may have a notification icon that shows the current number of unread
announcements.  Front-ends can mark an announcement as `read` by calling
the [dismiss announcement](#dismiss-an-announcement) endpoint.  Any
announcement entry with `dismissed == true` should be considered read.

When a `high priority` announcement is detected it is recommended that
clients present the announcement in a format that is immediately visible
to the user.  That said, it may be wise to allow users to opt out of
this behavior via configuration.

/// note
If an announcement is dismissed, closed on GitHub, then reopened,
the `dismissed` flag will reset to false.  This is expected behavior
as announcements are pruned from the database when they are no
longer present in feeds.  It isn't valid for repo maintainers
to re-open a closed announcement.  That said, its fine to close
and re-open issues during development and testing using repos
that are not yet registered with moonlight.
///
