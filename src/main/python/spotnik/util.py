from __future__ import print_function, absolute_import, division


def _boto_tags_to_dict(tags):
    """Convert the Tags in boto format into a usable dict

    [{'Key': 'foo', 'Value': 'bar'}, {'Key': 'ham', 'Value': 'spam'}]
    is translated to
    {'foo': 'bar', 'ham': 'spam'}
    """
    return {item['Key']: item['Value'] for item in tags}
